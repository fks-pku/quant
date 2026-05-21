"""A-share mid-cap dividend low-volatility capacity strategy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


@strategy("ashare_mid_cap_dividend_low_vol_capacity")
class AShareMidCapDividendLowVolCapacityStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 20,
        max_positions: int = 50,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.30,
        cap_percentile_high: float = 0.85,
        min_price: float = 5.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
        volatility_lookback: int = 60,
    ):
        self.volatility_lookback = max(2, int(volatility_lookback))
        super().__init__(
            "ashare_mid_cap_dividend_low_vol_capacity",
            symbols=symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=self.volatility_lookback,
        )

    @property
    def formula_key(self) -> str:
        return "ashare_mid_cap_dividend_low_vol_capacity"

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "dv_ttm", "pb", "turnover_rate_f", "adj_close"]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        return [
            ("dv_ttm", 0.35, True),
            ("volatility", 0.25, False),
            ("pb", 0.20, False),
            ("circ_mv", 0.10, True),
            ("turnover_rate", 0.10, False),
        ]

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        dv_ttm = self._positive_float(self._value(bar, "dv_ttm"))
        pb = self._positive_float(self._value(bar, "pb"))
        if dv_ttm <= 0:
            return {"symbol": symbol, "missing_field": "dv_ttm"}
        if pb <= 0:
            return {"symbol": symbol, "missing_field": "pb"}
        volatility = self._volatility(symbol, self.volatility_lookback)
        if volatility is None:
            return {"symbol": symbol, "missing_field": "volatility"}
        turnover_rate = float(base.get("turnover_rate") or 0.0)
        if turnover_rate <= 0:
            return {"symbol": symbol, "missing_field": "turnover_rate"}
        return {
            **base,
            "dv_ttm": dv_ttm,
            "pb": pb,
            "volatility": volatility,
            "turnover_rate": turnover_rate,
            "missing_field": "",
        }

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update({"volatility_lookback": self.volatility_lookback})
        return params
