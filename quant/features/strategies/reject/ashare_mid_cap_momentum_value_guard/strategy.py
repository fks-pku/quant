"""A-share mid-cap 12-1 momentum strategy with value guards."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


@strategy("ashare_mid_cap_momentum_value_guard")
class AShareMidCapMomentumValueGuardStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 20,
        max_positions: int = 50,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.30,
        cap_percentile_high: float = 0.80,
        min_price: float = 5.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
        momentum_lookback: int = 252,
        momentum_skip: int = 21,
        volatility_lookback: int = 120,
    ):
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.volatility_lookback = max(2, int(volatility_lookback))
        super().__init__(
            "ashare_mid_cap_momentum_value_guard",
            symbols=symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=max(self.momentum_lookback, self.volatility_lookback),
        )

    @property
    def formula_key(self) -> str:
        return "ashare_mid_cap_momentum_value_guard"

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "pb", "ps_ttm", "adj_close"]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        return [
            ("momentum", 0.45, True),
            ("pb", 0.20, False),
            ("ps_ttm", 0.15, False),
            ("volatility", 0.10, False),
            ("circ_mv", 0.10, True),
        ]

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pb = self._positive_float(self._value(bar, "pb"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm"))
        if pb <= 0:
            return {"symbol": symbol, "missing_field": "pb"}
        if ps_ttm <= 0:
            return {"symbol": symbol, "missing_field": "ps_ttm"}
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        volatility = self._volatility(symbol, self.volatility_lookback)
        if volatility is None:
            return {"symbol": symbol, "missing_field": "volatility"}
        return {
            **base,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "momentum": momentum,
            "volatility": volatility,
            "missing_field": "",
        }

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
            {
                "momentum_lookback": self.momentum_lookback,
                "momentum_skip": self.momentum_skip,
                "volatility_lookback": self.volatility_lookback,
            }
        )
        return params
