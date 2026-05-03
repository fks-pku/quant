"""Dual Momentum Strategy - Gary Antonacci style absolute + relative momentum.

Goes long risky asset when BOTH conditions hold:
  1. Absolute momentum: asset return over lookback > 0 (trending up)
  2. Relative momentum: asset return > safe asset return over same lookback

If absolute momentum is negative, rotates to safe asset (bonds/cash equivalent).
Monthly rebalance to minimize turnover.

Hypothesis: Dual momentum captures upside in bull markets while protecting
drawdowns in bear markets via systematic rotation to safe assets. Antonacci
(2014) shows this simple approach outperforms buy-and-hold with lower drawdown.

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("DualMomentum")
class DualMomentum(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        safe_symbol: str = "TLT",
        lookback_months: int = 12,
        holding_days: int = 21,
        max_position_pct: float = 0.90,
    ):
        _trading_syms = symbols or ["SPY", "QQQ", "IWM", "VGK", "EEM"]
        self._trading_symbols = _trading_syms
        self.safe_symbol = safe_symbol
        self.lookback_months = lookback_months
        self.max_position_pct = max_position_pct

        _all_syms = list(set(_trading_syms + [safe_symbol]))
        super().__init__("DualMomentum", _all_syms, holding_days)

        self._current_allocation: Optional[str] = None

    @property
    def _max_keep_hint(self) -> int:
        return self.lookback_months * 21 + 10

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("DualMomentum")
        self.logger.info(
            f"DualMomentum starting with lookback={self.lookback_months}m, "
            f"safe_asset={self.safe_symbol}"
        )

    def _calculate_returns(self, symbol: str, lookback_days: int) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < lookback_days + 1:
            return 0.0
        past = closes[-lookback_days - 1]
        if past <= 0:
            return 0.0
        return (closes[-1] - past) / past

    def _select_best_asset(self) -> Optional[str]:
        lookback_days = self.lookback_months * 21

        best_symbol = None
        best_return = -np.inf

        for symbol in self._trading_symbols:
            ret = self._calculate_returns(symbol, lookback_days)
            if ret > best_return:
                best_return = ret
                best_symbol = symbol

        absolute_momentum_positive = best_return > 0

        safe_ret = self._calculate_returns(self.safe_symbol, lookback_days)
        relative_momentum_positive = best_return > safe_ret

        if not absolute_momentum_positive:
            return self.safe_symbol

        if relative_momentum_positive:
            return best_symbol

        return self.safe_symbol

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        target_symbol = self._select_best_asset()
        if target_symbol is None:
            return

        nav = context.portfolio.nav

        for symbol in list(self._positions.keys()):
            pos_qty = self._positions.get(symbol, 0)
            if pos_qty > 0 and symbol != target_symbol:
                self.sell(symbol, pos_qty)

        if target_symbol != self._current_allocation:
            price = self._get_last_price(target_symbol)
            if price > 0:
                qty = int((nav * self.max_position_pct) / price)
                if qty > 0:
                    self.buy(target_symbol, qty)

        self._current_allocation = target_symbol

        self.logger.info(
            f"DualMomentum rebalanced to {target_symbol}"
        )

    def _on_stop_cleanup(self) -> None:
        self._current_allocation = None

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "safe_symbol": self.safe_symbol,
            "lookback_months": self.lookback_months,
            "holding_days": self.holding_days,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "current_allocation": self._current_allocation,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
        }
