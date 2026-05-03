"""Daily Return Anomaly Strategy - Consecutive return pattern prediction.

Based on Cakici et al. (2026) "A Unified Framework for Anomalies based on Daily Returns".

Core logic:
  1. Count consecutive positive (or negative) return days for each stock
  2. Short-term (5-day): follow the streak -- momentum continuation
  3. Medium-term (20-day): bet on reversal -- after extended streaks, mean reversion

Signal = short_term_signal * 0.6 + medium_term_signal * 0.4

Hypothesis: Investors under-react to consecutive information in short term (attention
bias), creating momentum. But extended streaks lead to over-extension and reversal.
A-Shares with retail dominance amplify these behavioral biases.

Source: Nusret Cakici et al. (Jan 2026), Alpha Architect
Authors: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("DailyReturnAnomaly")
class DailyReturnAnomaly(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        streak_short: int = 3,
        streak_long: int = 8,
        short_weight: float = 0.6,
        holding_days: int = 5,
        top_pct: float = 0.2,
        max_position_pct: float = 0.10,
    ):
        _syms = symbols or [
            "600519", "000858", "601318", "600036", "000333",
            "002415", "300750", "601012", "600900", "000651",
            "002304", "600276", "601888", "000568", "603259",
        ]
        super().__init__("DailyReturnAnomaly", _syms, holding_days)
        self.streak_short = streak_short
        self.streak_long = streak_long
        self.short_weight = short_weight
        self.top_pct = top_pct
        self.max_position_pct = max_position_pct

        self._long_positions: List[str] = []

    @property
    def _max_keep_hint(self) -> int:
        return 60

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("DailyReturnAnomaly")

    def _count_streak(self, symbol: str) -> int:
        closes = self._get_closes(symbol)
        if len(closes) < 2:
            return 0
        streak = 0
        last_dir = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] == 0 or closes[i - 1] == 0:
                break
            direction = 1 if closes[i] > closes[i - 1] else -1
            if last_dir == 0:
                last_dir = direction
                streak = 1
            elif direction == last_dir:
                streak += 1
            else:
                break
        return streak * last_dir

    def _calculate_short_term_signal(self, streak: int) -> float:
        if abs(streak) >= self.streak_short:
            return float(np.sign(streak)) * min(abs(streak) / 5.0, 1.0)
        return 0.0

    def _calculate_medium_term_signal(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < 21:
            return 0.0
        if closes[-21] <= 0:
            return 0.0
        ret_20 = (closes[-1] / closes[-21]) - 1
        if ret_20 > 0.15:
            return -0.5
        elif ret_20 < -0.15:
            return 0.5
        return 0.0

    def _calculate_composite_scores(self) -> Dict[str, float]:
        scores = {}
        for symbol in self._symbols:
            streak = self._count_streak(symbol)
            short_signal = self._calculate_short_term_signal(streak)
            medium_signal = self._calculate_medium_term_signal(symbol)
            scores[symbol] = (
                short_signal * self.short_weight
                + medium_signal * (1.0 - self.short_weight)
            )
        return scores

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        scores = self._calculate_composite_scores()
        if not scores:
            return

        sorted_syms = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        n_top = max(1, int(len(sorted_syms) * self.top_pct))
        new_long = [s[0] for s in sorted_syms[:n_top] if s[1] > 0]

        for sym in list(self._long_positions):
            if sym not in new_long:
                pos_qty = self._positions.get(sym, 0)
                if pos_qty > 0:
                    self.sell(sym, pos_qty)

        self._long_positions = new_long
        nav = context.portfolio.nav
        weight = self.max_position_pct / len(new_long) if new_long else 0

        for symbol in new_long:
            if self._positions.get(symbol, 0) > 0:
                continue
            price = self._get_last_price(symbol)
            if price > 0:
                qty = int((nav * weight) / price)
                if qty > 0:
                    self.buy(symbol, qty)

    def _on_stop_cleanup(self) -> None:
        self._long_positions.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "streak_short": self.streak_short,
            "streak_long": self.streak_long,
            "short_weight": self.short_weight,
            "holding_days": self.holding_days,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "long_positions": self._long_positions,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
        }
