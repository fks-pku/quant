"""A-share mid-cap low-volatility value strategy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


@strategy("ashare_mid_cap_low_vol_value")
class AShareMidCapLowVolValueStrategy(AShareMidCapCompositeBase):
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
        volatility_lookback: int = 60,
        drawdown_lookback: int = 60,
    ):
        self.volatility_lookback = max(2, int(volatility_lookback))
        self.drawdown_lookback = max(2, int(drawdown_lookback))
        super().__init__(
            "ashare_mid_cap_low_vol_value",
            symbols=symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=max(self.volatility_lookback, self.drawdown_lookback),
        )

    @property
    def formula_key(self) -> str:
        return "ashare_mid_cap_low_vol_value"

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "pe_ttm", "pb", "ps_ttm", "adj_close"]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        return [
            ("pb", 0.25, False),
            ("pe_ttm", 0.20, False),
            ("ps_ttm", 0.15, False),
            ("volatility", 0.25, False),
            ("drawdown", 0.15, True),
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
        volatility = self._volatility(symbol, self.volatility_lookback)
        if volatility is None:
            return {"symbol": symbol, "missing_field": "volatility"}
        drawdown = self._max_drawdown(symbol, self.drawdown_lookback)
        if drawdown is None:
            return {"symbol": symbol, "missing_field": "drawdown"}
        return {
            **base,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "volatility": volatility,
            "drawdown": drawdown,
            "missing_field": "",
        }

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
            {
                "volatility_lookback": self.volatility_lookback,
                "drawdown_lookback": self.drawdown_lookback,
            }
        )
        return params
