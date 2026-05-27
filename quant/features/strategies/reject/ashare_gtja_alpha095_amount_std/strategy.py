"""GTJA Alpha191 factor 095 A-share candidate strategy."""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_gtja_alpha095_amount_std"


@strategy(STRATEGY_NAME)
class AShareGtjaAlpha095AmountStdStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 5,
        max_positions: int = 40,
        max_position_pct: float = 0.95,
        cap_percentile_low: float = 0.25,
        cap_percentile_high: float = 1.0,
        min_price: float = 3.0,
        min_turnover: float = 200000.0,
        lot_size: int = 100,
        target_weight_slots: int = 40,
        amount_lookback: int = 20,
        alpha_high_is_better: bool = True,
        benchmark_symbol: str = "000300",
    ):
        self.amount_lookback = max(2, int(amount_lookback))
        self.alpha_high_is_better = bool(alpha_high_is_better)
        self.benchmark_symbol = str(benchmark_symbol)
        tradable_symbols = [str(symbol) for symbol in symbols or [] if str(symbol) != self.benchmark_symbol]
        super().__init__(
            STRATEGY_NAME,
            symbols=tradable_symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=self.amount_lookback,
            target_weight_slots=target_weight_slots,
        )

    @property
    def formula_key(self) -> str:
        return STRATEGY_NAME

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "turnover", "volume", "adj_close"]

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [("alpha095", 1.0, self.alpha_high_is_better)]

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        alpha095 = self._amount_std(symbol)
        if alpha095 is None:
            return {"symbol": symbol, "missing_field": "alpha095"}
        return {
            **base,
            "alpha095": alpha095,
            "amount_std_20": alpha095,
            "missing_field": "",
        }

    def _amount_std(self, symbol: str) -> Optional[float]:
        bars = self._day_data.get(symbol, [])[-self.amount_lookback :]
        if len(bars) < self.amount_lookback:
            return None
        values = [self._bar_amount(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        if len(values) < self.amount_lookback:
            return None
        std_value = statistics.stdev(values)
        return std_value if math.isfinite(std_value) and std_value > 0 else None

    def _bar_amount(self, bar: Any) -> float:
        amount = self._positive_float(self._value(bar, "amount"))
        if amount > 0:
            return amount
        turnover = self._positive_float(self._value(bar, "turnover"))
        if turnover > 0:
            return turnover
        return self._price(bar) * self._positive_float(self._value(bar, "volume"))

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
            {
                "amount_lookback": self.amount_lookback,
                "alpha_high_is_better": self.alpha_high_is_better,
                "benchmark_symbol": self.benchmark_symbol,
            }
        )
        return params
