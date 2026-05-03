"""Simple Momentum Strategy - Cross-sectional momentum on US equities.

This strategy ranks stocks by 20-day momentum and goes long the top decile
while going short the bottom decile, holding for 1 month before rebalancing.

Hypothesis: Stocks with strong recent momentum continue to outperform in the
short term, while losers continue to underperform. This is the "winner-minus-loser"
effect documented by Jegadeesh and Titman (1993).

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("SimpleMomentum")
class SimpleMomentum(DailyBarStrategy):
    """
    Cross-sectional momentum strategy.

    Ranks stocks by past returns and goes long top decile, short bottom decile.
    Monthly rebalancing to avoid excessive turnover.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        momentum_lookback: int = 20,
        holding_period: int = 21,
        top_pct: float = 0.1,
        bottom_pct: float = 0.1,
        max_position_pct: float = 0.05,
    ):
        self._symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
        self.momentum_lookback = momentum_lookback
        self.holding_period = holding_period
        self.top_pct = top_pct
        self.bottom_pct = bottom_pct
        self.max_position_pct = max_position_pct

        self._momentum_scores: Dict[str, float] = {}
        self._long_positions: List[str] = []
        self._short_positions: List[str] = []

        super().__init__("SimpleMomentum", self._symbols, holding_days=1)

    @property
    def _max_keep_hint(self) -> int:
        return self.momentum_lookback * 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("SimpleMomentum")
        self.logger.info(
            f"SimpleMomentum starting with lookback={self.momentum_lookback}, "
            f"holding_period={self.holding_period}"
        )

    def _calculate_momentum_scores(self) -> None:
        self._momentum_scores.clear()

        for symbol in self._symbols:
            if symbol in self._day_data and len(self._day_data[symbol]) >= self.momentum_lookback:
                prices = []
                for bar in self._day_data[symbol]:
                    prices.append(self._adj(bar, "close"))
                if len(prices) >= self.momentum_lookback:
                    current_price = prices[-1]
                    past_price = prices[-self.momentum_lookback]
                    if past_price > 0:
                        momentum = (current_price - past_price) / past_price
                        self._momentum_scores[symbol] = momentum
                    else:
                        self._momentum_scores[symbol] = 0.0
                else:
                    self._momentum_scores[symbol] = 0.0
            else:
                self._momentum_scores[symbol] = 0.0

    def on_data(self, context: "Context", data: Any) -> None:
        close = data.get("close") if isinstance(data, dict) else getattr(data, "close", None)
        if not close:
            return
        super().on_data(context, data)

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        self._calculate_momentum_scores()

    def execute(self, context: "Context", trading_date: Optional[date] = None) -> None:
        if trading_date is None:
            trading_date = date.today()

        if self._last_rebalance_date is not None:
            days_since_rebalance = (trading_date - self._last_rebalance_date).days
            if days_since_rebalance < self.holding_period:
                return

        self._execute_rebalance(context, trading_date)

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        self._calculate_momentum_scores()

        if not self._momentum_scores:
            self._last_rebalance_date = trading_date
            return

        sorted_by_momentum = sorted(
            self._momentum_scores.items(), key=lambda x: x[1], reverse=True
        )

        n_stocks = len(sorted_by_momentum)
        nav = context.portfolio.nav

        if n_stocks == 1:
            symbol, score = sorted_by_momentum[0]
            price = self._get_last_price(symbol)
            if price <= 0:
                self._last_rebalance_date = trading_date
                return
            if score > 0:
                self.buy(symbol, int(nav / price * 0.95))
                self._long_positions = [symbol]
            elif score < 0:
                self._close_position(context, symbol, int(nav / price))
                self._long_positions = []
            self._short_positions = []
        else:
            n_long = max(1, int(n_stocks * self.top_pct))
            n_short = max(1, int(n_stocks * self.bottom_pct))

            new_long = [s[0] for s in sorted_by_momentum[:n_long]]
            new_short = [s[0] for s in sorted_by_momentum[-n_short:]]

            for sym in list(self._long_positions):
                if sym not in new_long:
                    self._close_position(context, sym, self._positions.get(sym, 0))
            for sym in list(self._short_positions):
                if sym not in new_short:
                    pos_qty = self._positions.get(sym, 0)
                    if pos_qty > 0:
                        self.sell(sym, pos_qty)

            self._long_positions = new_long
            self._short_positions = new_short

            long_weight = self.max_position_pct / n_long if n_long > 0 else 0
            short_weight = self.max_position_pct / n_short if n_short > 0 else 0

            for symbol in self._long_positions:
                price = self._get_last_price(symbol)
                if price > 0:
                    quantity = int((nav * long_weight) / price)
                    if quantity > 0:
                        self.buy(symbol, quantity)

            for symbol in self._short_positions:
                price = self._get_last_price(symbol)
                if price > 0:
                    quantity = int((nav * short_weight) / price)
                    if quantity > 0:
                        self.sell(symbol, quantity)

        self._last_rebalance_date = trading_date

        self.logger.info(
            f"SimpleMomentum rebalanced: long={self._long_positions}, short={self._short_positions}"
        )

    def _close_position(self, context: "Context", symbol: str, quantity: int) -> None:
        pos_qty = self._positions.get(symbol, 0)
        if pos_qty > 0:
            sell_qty = min(quantity, pos_qty)
            self.sell(symbol, sell_qty)

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)
        self.logger.info(
            f"SimpleMomentum filled: {fill.side} {fill.quantity} {fill.symbol}"
        )

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self.execute(context, trading_date)

    def _on_stop_cleanup(self) -> None:
        self._momentum_scores.clear()
        self._long_positions.clear()
        self._short_positions.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "momentum_lookback": self.momentum_lookback,
            "holding_period": self.holding_period,
            "top_pct": self.top_pct,
            "bottom_pct": self.bottom_pct,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "long_positions": self._long_positions,
            "short_positions": self._short_positions,
            "momentum_scores": self._momentum_scores,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
        }
