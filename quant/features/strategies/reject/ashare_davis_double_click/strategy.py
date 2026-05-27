"""A-share Davis Double Click candidate strategy."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


@strategy("ashare_davis_double_click")
class AShareDavisDoubleClickStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 20,
        max_positions: int = 10,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.25,
        cap_percentile_high: float = 0.95,
        min_price: float = 3.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
        momentum_lookback: int = 126,
        momentum_skip: int = 5,
        min_pe_ttm: float = 5.0,
        max_pe_ttm: float = 60.0,
        min_profit_growth: float = 15.0,
        min_roe: float = 6.0,
        min_momentum: float = -0.05,
    ):
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.min_pe_ttm = float(min_pe_ttm)
        self.max_pe_ttm = float(max_pe_ttm)
        self.min_profit_growth = float(min_profit_growth)
        self.min_roe = float(min_roe)
        self.min_momentum = float(min_momentum)
        super().__init__(
            "ashare_davis_double_click",
            symbols=symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=self.momentum_lookback,
            target_weight_slots=max_positions,
        )

    @property
    def formula_key(self) -> str:
        return "ashare_davis_double_click"

    @property
    def required_fields(self) -> List[str]:
        return [
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "roe",
            "q_roe",
            "netprofit_yoy",
            "q_netprofit_yoy",
            "or_yoy",
            "q_sales_yoy",
            "adj_close",
        ]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        return [
            ("growth_to_pe", 0.30, True),
            ("profit_growth", 0.25, True),
            ("roe", 0.15, True),
            ("earnings_yield", 0.15, True),
            ("momentum", 0.10, True),
            ("sales_growth", 0.05, True),
        ]

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm"))
        if pe_ttm <= 0:
            return {"symbol": symbol, "missing_field": "pe_ttm"}
        if pe_ttm < self.min_pe_ttm:
            return {"symbol": symbol, "rejection_reason": "pe_too_low"}
        if pe_ttm > self.max_pe_ttm:
            return {"symbol": symbol, "rejection_reason": "pe_too_high"}

        profit_growth = self._first_finite(bar, "q_netprofit_yoy", "netprofit_yoy")
        if profit_growth is None:
            return {"symbol": symbol, "missing_field": "profit_growth"}
        if profit_growth < self.min_profit_growth:
            return {"symbol": symbol, "rejection_reason": "weak_profit_growth"}

        roe = self._first_finite(bar, "q_roe", "roe")
        if roe is None:
            return {"symbol": symbol, "missing_field": "roe"}
        if roe < self.min_roe:
            return {"symbol": symbol, "rejection_reason": "weak_roe"}

        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        if momentum < self.min_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_momentum"}

        sales_growth = self._first_finite(bar, "q_sales_yoy", "or_yoy")
        sales_growth = 0.0 if sales_growth is None else sales_growth
        clipped_growth = self._clip(profit_growth, -100.0, 200.0)
        return {
            **base,
            "pe_ttm": pe_ttm,
            "profit_growth": clipped_growth,
            "sales_growth": self._clip(sales_growth, -100.0, 200.0),
            "roe": self._clip(roe, -50.0, 80.0),
            "earnings_yield": 1.0 / pe_ttm,
            "growth_to_pe": max(clipped_growth, 0.0) / pe_ttm,
            "momentum": momentum,
            "missing_field": "",
        }

    @staticmethod
    def _first_finite(data: Any, *fields: str) -> Optional[float]:
        for field in fields:
            value = AShareMidCapCompositeBase._value(data, field)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                return number
        return None

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return min(max(float(value), float(low)), float(high))

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
            {
                "momentum_lookback": self.momentum_lookback,
                "momentum_skip": self.momentum_skip,
                "min_pe_ttm": self.min_pe_ttm,
                "max_pe_ttm": self.max_pe_ttm,
                "min_profit_growth": self.min_profit_growth,
                "min_roe": self.min_roe,
                "min_momentum": self.min_momentum,
            }
        )
        return params
