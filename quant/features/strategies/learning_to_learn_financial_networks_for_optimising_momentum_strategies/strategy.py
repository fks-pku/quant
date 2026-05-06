"""Learning to Learn Financial Networks for Optimising Momentum Strategies

Source: arxiv (http://arxiv.org/abs/2308.12212v1)
Authors: Xingyue Pu
Type: momentum
Summary: L2GMOM uses end-to-end deep learning to learn financial networks and optimize
momentum portfolios. This simplified version uses covariance-based network weighting
to construct a cross-sectional momentum portfolio — assets more connected in the
return covariance network receive higher portfolio weights.
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("LearningFinancialNetworksMomentumStrategy")
class LearningFinancialNetworksMomentumStrategy(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        momentum_period: int = 20,
        covariance_period: int = 60,
        top_k: int = 2,
        holding_days: int = 5,
        max_position_pct: float = 0.90,
    ):
        self._symbols = symbols or ["SPY", "QQQ", "IWM", "GLD", "TLT"]
        self.momentum_period = momentum_period
        self.covariance_period = covariance_period
        self.top_k = top_k
        self.max_position_pct = max_position_pct
        self._current_holdings: List[str] = []

        super().__init__(
            "LearningFinancialNetworksMomentumStrategy",
            self._symbols,
            holding_days=holding_days,
        )

    @property
    def _max_keep_hint(self) -> int:
        return max(self.covariance_period + 5, self.momentum_period + 5) * 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("LearningFinancialNetworksMomentumStrategy")
        self.logger.info(
            "L2G-MOM starting: momentum=%d cov=%d top_k=%d holding=%d symbols=%s",
            self.momentum_period,
            self.covariance_period,
            self.top_k,
            self.holding_days,
            self._symbols,
        )

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        min_bars = max(self.covariance_period, self.momentum_period) + 1
        bar_counts = {s: len(self._day_data.get(s, [])) for s in self._symbols}
        if any(c < min_bars for c in bar_counts.values()):
            return

        closes_matrix: Dict[str, np.ndarray] = {}
        returns_matrix: Dict[str, np.ndarray] = {}
        for sym in self._symbols:
            bars = self._day_data[sym]
            closes = np.array([self._adj(b, "close") for b in bars])
            closes_matrix[sym] = closes
            ret = np.diff(closes) / closes[:-1]
            ret = np.where(np.isfinite(ret), ret, 0.0)
            returns_matrix[sym] = ret

        momentum_scores: Dict[str, float] = {}
        for sym in self._symbols:
            closes = closes_matrix[sym]
            p_now = closes[-1]
            p_past = closes[-self.momentum_period - 1]
            if p_past > 0:
                momentum_scores[sym] = (p_now - p_past) / p_past
            else:
                momentum_scores[sym] = 0.0

        symbols_list = list(self._symbols)
        n = len(symbols_list)
        ret_window = np.column_stack(
            [returns_matrix[s][-self.covariance_period:] for s in symbols_list]
        )
        cov_mat = np.cov(ret_window, rowvar=False)
        if cov_mat.ndim != 2 or cov_mat.shape != (n, n):
            return

        abs_cov = np.abs(cov_mat)
        np.fill_diagonal(abs_cov, 0.0)
        network_weights_arr = abs_cov.sum(axis=1)
        total_weight = network_weights_arr.sum()
        if total_weight > 0:
            network_weights_arr = network_weights_arr / total_weight

        network_weights: Dict[str, float] = {
            symbols_list[i]: network_weights_arr[i] for i in range(n)
        }

        adjusted: Dict[str, float] = {
            sym: momentum_scores[sym] * network_weights.get(sym, 0.0)
            for sym in self._symbols
        }

        ranked = sorted(adjusted.keys(), key=lambda s: adjusted[s], reverse=True)
        target_long = set(ranked[: self.top_k])

        for sym in self._current_holdings:
            if sym not in target_long:
                pos = self._positions.get(sym, 0)
                if pos > 0:
                    self.sell(sym, int(pos))

        nav = context.portfolio.nav
        if nav <= 0:
            return

        per_symbol_alloc = nav * self.max_position_pct / max(len(target_long), 1)

        for sym in target_long:
            price = self._get_last_price(sym)
            if price <= 0:
                continue
            current_pos = self._positions.get(sym, 0)
            desired_shares = int(per_symbol_alloc / price)
            delta = desired_shares - int(current_pos)
            if delta > 0:
                self.buy(sym, delta)

        self._current_holdings = list(target_long)

    def _on_stop_cleanup(self) -> None:
        self._current_holdings.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbols": self._symbols,
            "momentum_period": self.momentum_period,
            "covariance_period": self.covariance_period,
            "top_k": self.top_k,
            "holding_days": self.holding_days,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {"current_holdings": list(self._current_holdings)}
