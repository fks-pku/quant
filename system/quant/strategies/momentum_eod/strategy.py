"""Momentum EOD strategy - buy top-5 S&P 500 gainers at open, sell at close.

Educational example strategy.
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING
import pandas as pd

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("MomentumEOD")
class MomentumEOD(Strategy):
    """
    Buy top-5 S&P 500 gainers at market open, sell at market close.
    Educational example - not for live trading.
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        super().__init__("MomentumEOD")
        self._symbols = symbols or ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "SPY"]
        self.top_n = 5
        self._day_data: Dict[str, List] = {}
        self._opened_positions = False
        self._closed_positions = False

    @property
    def symbols(self) -> List[str]:
        """List of symbols this strategy trades."""
        return self._symbols

    def on_start(self, context: "Context") -> None:
        """Initialize strategy."""
        super().on_start(context)
        self.logger = get_logger("MomentumEOD")

    def on_data(self, context: "Context", data) -> None:
        """
        Process incoming bar data.
        Buy momentum at open, sell at close.
        """
        if not hasattr(data, "symbol"):
            return

        symbol = data.symbol
        if symbol not in self.symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []

        self._day_data[symbol].append(data)

    def on_fill(self, context: "Context", fill: Any) -> None:
        """Track filled orders."""
        super().on_fill(context, fill)
        self.logger.info(f"MomentumEOD filled: {fill.side} {fill.quantity} {fill.symbol}")

    def execute_open(self, context: "Context") -> None:
        """
        Select top-N momentum stocks and buy.
        Called at market open via scheduler.
        """
        if self._opened_positions:
            return

        returns = {}
        for symbol, bars in self._day_data.items():
            if len(bars) >= 2:
                open_price = bars[0].open if hasattr(bars[0], "open") else bars[0]["open"]
                current_price = bars[-1].close if hasattr(bars[-1], "close") else bars[-1]["close"]
                returns[symbol] = (current_price - open_price) / open_price

        sorted_by_return = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        top_symbols = [s[0] for s in sorted_by_return[:self.top_n]]

        nav = context.portfolio.nav
        max_position_pct = 0.05

        for symbol in top_symbols:
            price = returns.get(symbol, 100)
            quantity = int((nav * max_position_pct) / price)
            if quantity > 0:
                self.buy(symbol, quantity)

        self._opened_positions = True

    def execute_close(self, context: "Context") -> None:
        """
        Close all positions at market close.
        Called at market close via scheduler.
        """
        if self._closed_positions:
            return

        positions = context.portfolio.get_all_positions()
        for pos in positions:
            if pos.symbol in self.symbols:
                self.sell(pos.symbol, pos.quantity)

        self._closed_positions = True

    def on_stop(self, context: "Context") -> None:
        """Cleanup on strategy stop."""
        self._day_data.clear()
        self._opened_positions = False
        self._closed_positions = False
