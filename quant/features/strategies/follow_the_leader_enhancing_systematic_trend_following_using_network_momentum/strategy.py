"""Follow the Leader: Enhancing Systematic Trend-Following Using Network Momentum

Source: arxiv (http://arxiv.org/abs/2501.07135v1)
Authors: Linze Li
Type: momentum
Summary: A cross-asset trend-following strategy that exploits documented lead-lag
relationships in commodity futures via network momentum signals, offering a genuine
enhancement over univariate trend indicators but with moderate implementation
complexity and overfitting risk from multiple estimation layers.
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy")
class FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        trend_period: int = 50,
        correlation_period: int = 60,
        alpha: float = 0.6,
        max_position_pct: float = 0.90,
    ):
        self._symbols = symbols or ["SPY", "GLD", "TLT", "IWM", "QQQ"]
        self.trend_period = trend_period
        self.correlation_period = correlation_period
        self.alpha = alpha
        self.max_position_pct = max_position_pct
        self._prev_signals: Dict[str, str] = {}

        super().__init__(
            "FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy",
            self._symbols,
            holding_days=5,
        )

    @property
    def _max_keep_hint(self) -> int:
        return max(self.trend_period, self.correlation_period) * 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("FollowTheLeaderNetworkMomentum")
        self.logger.info(
            "FollowTheLeader starting: trend=%d corr=%d alpha=%.2f symbols=%s",
            self.trend_period, self.correlation_period, self.alpha, self._symbols,
        )

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        min_bars = max(self.trend_period, self.correlation_period) + 1
        closes_by_symbol: Dict[str, np.ndarray] = {}
        for symbol in self._symbols:
            closes_arr = np.array(self._get_closes(symbol))
            if len(closes_arr) < min_bars:
                return
            closes_by_symbol[symbol] = closes_arr

        trend_signals: Dict[str, float] = {}
        for symbol in self._symbols:
            closes_arr = closes_by_symbol[symbol]
            ma = np.mean(closes_arr[-self.trend_period:])
            current_price = closes_arr[-1]
            trend_signals[symbol] = (current_price - ma) / ma if ma > 0 else 0.0

        returns_by_symbol: Dict[str, np.ndarray] = {}
        for symbol in self._symbols:
            closes_arr = closes_by_symbol[symbol]
            returns_by_symbol[symbol] = np.diff(closes_arr[-(self.correlation_period + 1):]) / closes_arr[-(self.correlation_period + 1):-1]

        return_matrix = np.column_stack([returns_by_symbol[s] for s in self._symbols])
        if return_matrix.shape[0] < 2:
            return
        corr_matrix = np.corrcoef(return_matrix.T)
        if corr_matrix.ndim != 2 or corr_matrix.shape[0] != len(self._symbols):
            return

        network_signals: Dict[str, float] = {}
        for i, symbol in enumerate(self._symbols):
            weighted_sum = 0.0
            weight_total = 0.0
            for j, other in enumerate(self._symbols):
                if i == j:
                    continue
                corr_val = abs(corr_matrix[i, j])
                weighted_sum += corr_val * trend_signals[other]
                weight_total += corr_val
            network_signals[symbol] = weighted_sum / weight_total if weight_total > 0 else 0.0

        for symbol in self._symbols:
            univariate = trend_signals[symbol]
            network = network_signals[symbol]
            combined = self.alpha * univariate + (1.0 - self.alpha) * network
            current_pos = self._positions.get(symbol, 0)
            price = self._get_last_price(symbol)
            if price <= 0:
                continue

            if combined > 0 and current_pos == 0:
                nav = context.portfolio.nav
                if nav <= 0:
                    continue
                signal_strength = min(abs(combined) * 10.0, 1.0)
                qty = int(nav * self.max_position_pct * signal_strength / price / len(self._symbols))
                if qty > 0:
                    self.buy(symbol, qty)
                    self._prev_signals[symbol] = "long"
                    self.logger.info(
                        "LONG %s: uni=%.4f net=%.4f comb=%.4f str=%.2f -> BUY %d @ ~%.2f",
                        symbol, univariate, network, combined, signal_strength, qty, price,
                    )

            elif combined < 0 and current_pos > 0:
                self.sell(symbol, int(current_pos))
                self._prev_signals[symbol] = "close"
                self.logger.info(
                    "CLOSE %s: uni=%.4f net=%.4f comb=%.4f -> SELL %d",
                    symbol, univariate, network, combined, int(current_pos),
                )

    def _on_stop_cleanup(self) -> None:
        self._prev_signals.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "trend_period": self.trend_period,
            "correlation_period": self.correlation_period,
            "alpha": self.alpha,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {"prev_signals": dict(self._prev_signals)}
