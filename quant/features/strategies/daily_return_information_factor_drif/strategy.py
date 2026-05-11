"""Daily Return Information Factor DRIF

Source: ssrn (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6005614)
Authors: Nusret Cakici; Christian Fieberg; Gabor Neszveda; Robert J. Bianchi; Adam Zaremba
Type: mean_reversion
Formula: mean_reversion_close_to_ma
Summary: mean_reversion idea triaged by deterministic professional rubric
"""

from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("daily_return_information_factor_drif")
class DailyReturnInformationFactorDrifStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        lookback: int = 20,
        holding_days: int = 5,
        max_position_pct: float = 0.10,
    ):
        self._symbols = symbols or ["000300", "000905", "600519", "000001", "510300"]
        self.lookback = lookback
        self.max_position_pct = max_position_pct
        super().__init__("daily_return_information_factor_drif", self._symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return max(self.lookback * 3, self.lookback + 5)

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("DailyReturnInformationFactorDrifStrategy")

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        candidates = []
        for symbol in self._symbols:
            signal = self._signal(symbol)
            price = self._get_last_price(symbol)
            current_pos = self._positions.get(symbol, 0)
            if signal <= 0 and current_pos > 0:
                self.sell(symbol, int(current_pos), "MARKET", price if price > 0 else None)
            elif signal > 0 and price > 0:
                candidates.append((signal, symbol, price))
        if not candidates:
            return
        candidates.sort(reverse=True)
        slots = max(1, len(candidates))
        for _, symbol, price in candidates:
            target_qty = self._target_quantity(context, price, slots)
            current_pos = self._positions.get(symbol, 0)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _target_quantity(self, context: "Context", price: float, slots: int) -> int:
        portfolio = getattr(context, "portfolio", None)
        nav = float(getattr(portfolio, "nav", 0.0) or 0.0)
        if nav <= 0 or price <= 0:
            return 0
        return int((nav * self.max_position_pct / max(1, slots)) / price)

    def _signal(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < self.lookback:
            return 0.0
        current = closes[-1]
        moving_average = float(np.mean(closes[-self.lookback:]))
        if moving_average <= 0:
            return 0.0
        return float((moving_average - current) / moving_average)


    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "lookback": self.lookback,
            "max_position_pct": self.max_position_pct,
            "formula_key": "mean_reversion_close_to_ma",
        }
