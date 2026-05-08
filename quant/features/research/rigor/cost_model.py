import logging
from typing import Any, Dict

from quant.features.research.models import CostEstimate

logger = logging.getLogger(__name__)


def estimate_costs(
    trade_value: float,
    avg_daily_volume: float = 0.0,
    price: float = 100.0,
    volatility: float = 0.2,
    config: Dict[str, Any] = None,
) -> CostEstimate:
    config = config or {}
    spread_bps = config.get("spread_bps", 2.0)
    max_adv_pct = config.get("max_adv_pct", 0.05)

    commission = max(1.0, trade_value * 0.001)
    spread_cost = trade_value * spread_bps / 10000

    participation_rate = 0.0
    market_impact = 0.0
    capacity_ok = True
    capacity_adv_pct = 0.0

    if avg_daily_volume > 0 and price > 0:
        shares = trade_value / price
        adv_shares = avg_daily_volume
        participation_rate = shares / adv_shares if adv_shares > 0 else 0
        capacity_adv_pct = participation_rate
        if participation_rate > max_adv_pct:
            capacity_ok = False
        market_impact = trade_value * volatility * participation_rate * 0.1
    elif avg_daily_volume == 0 and trade_value > 0:
        capacity_ok = False

    total_bps = (commission + spread_cost + market_impact) / max(trade_value, 1) * 10000

    return CostEstimate(
        commission=commission,
        spread_cost=spread_cost,
        market_impact=market_impact,
        total_bps=total_bps,
        capacity_adv_pct=capacity_adv_pct,
        capacity_ok=capacity_ok,
    )
