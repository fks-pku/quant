"""A-share value momentum filter strategy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


@strategy("ashare_value_momentum_filter")
class AShareValueMomentumFilterStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 20,
        max_positions: int = 50,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.20,
        cap_percentile_high: float = 0.90,
        min_price: float = 5.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
        momentum_lookback: int = 252,
        momentum_skip: int = 21,
        recent_return_lookback: int = 21,
        max_recent_return: float = 0.30,
    ):
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.recent_return_lookback = max(2, int(recent_return_lookback))
        self.max_recent_return = float(max_recent_return)
        super().__init__(
            "ashare_value_momentum_filter",
            symbols=symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=max(self.momentum_lookback, self.recent_return_lookback),
        )

    @property
    def formula_key(self) -> str:
        return "ashare_value_momentum_filter"

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "pe_ttm", "pb", "ps_ttm", "turnover_rate_f", "adj_close"]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        return [
            ("momentum", 0.35, True),
            ("pb", 0.20, False),
            ("pe_ttm", 0.15, False),
            ("ps_ttm", 0.15, False),
            ("turnover_rate", 0.10, False),
            ("circ_mv", 0.05, True),
        ]

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm"))
        pb = self._positive_float(self._value(bar, "pb"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm"))
        if pe_ttm <= 0:
            return {"symbol": symbol, "missing_field": "pe_ttm"}
        if pb <= 0:
            return {"symbol": symbol, "missing_field": "pb"}
        if ps_ttm <= 0:
            return {"symbol": symbol, "missing_field": "ps_ttm"}
        turnover_rate = float(base.get("turnover_rate") or 0.0)
        if turnover_rate <= 0:
            return {"symbol": symbol, "missing_field": "turnover_rate"}
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        recent_return = self._return(symbol, self.recent_return_lookback)
        if recent_return is None:
            return {"symbol": symbol, "missing_field": "recent_return"}
        if recent_return > self.max_recent_return:
            return {"symbol": symbol, "rejection_reason": "recent_overheat"}
        return {
            **base,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "turnover_rate": turnover_rate,
            "momentum": momentum,
            "recent_return": recent_return,
            "missing_field": "",
        }

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
            {
                "momentum_lookback": self.momentum_lookback,
                "momentum_skip": self.momentum_skip,
                "recent_return_lookback": self.recent_return_lookback,
                "max_recent_return": self.max_recent_return,
            }
        )
        return params
