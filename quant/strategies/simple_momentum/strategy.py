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
import pandas as pd
import numpy as np

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("SimpleMomentum")
class SimpleMomentum(Strategy):
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
        super().__init__("SimpleMomentum")
        self._symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
        self.momentum_lookback = momentum_lookback
        self.holding_period = holding_period
        self.top_pct = top_pct
        self.bottom_pct = bottom_pct
        self.max_position_pct = max_position_pct

        self._momentum_scores: Dict[str, float] = {}
        self._day_data: Dict[str, List] = {}
        self._last_rebalance_date: Optional[date] = None
        self._long_positions: List[str] = []
        self._short_positions: List[str] = []

    @property
    def symbols(self) -> List[str]:
        return self._symbols

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
                    if isinstance(bar, dict):
                        prices.append(bar.get("close", 0))
                    elif hasattr(bar, "close"):
                        prices.append(bar.close)
                    else:
                        prices.append(0)
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
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            close = data.get("close")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
            close = getattr(data, "close", None)
        else:
            return

        if not symbol or not close:
            return

        if symbol not in self._symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []

        self._day_data[symbol].append(data)
        if len(self._day_data[symbol]) > self.momentum_lookback * 2:
            self._day_data[symbol] = self._day_data[symbol][-self.momentum_lookback:]

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
        n_long = max(1, int(n_stocks * self.top_pct))
        n_short = max(1, int(n_stocks * self.bottom_pct))

        self._long_positions = [s[0] for s in sorted_by_momentum[:n_long]]
        self._short_positions = [s[0] for s in sorted_by_momentum[-n_short:]]

        nav = context.portfolio.nav
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

    def _get_last_price(self, symbol: str) -> float:
        if symbol in self._day_data and len(self._day_data[symbol]) > 0:
            last_bar = self._day_data[symbol][-1]
            if isinstance(last_bar, dict):
                return float(last_bar.get("close", 0))
            if hasattr(last_bar, "close"):
                return float(last_bar.close)
        return 0.0

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)
        self.logger.info(
            f"SimpleMomentum filled: {fill.side} {fill.quantity} {fill.symbol}"
        )

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self.execute(context, trading_date)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
        self._momentum_scores.clear()
        self._day_data.clear()
        self._long_positions.clear()
        self._short_positions.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "long_positions": self._long_positions,
            "short_positions": self._short_positions,
            "momentum_scores": self._momentum_scores,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
            "parameters": {
                "momentum_lookback": self.momentum_lookback,
                "holding_period": self.holding_period,
                "top_pct": self.top_pct,
                "bottom_pct": self.bottom_pct,
                "max_position_pct": self.max_position_pct,
            },
        }
