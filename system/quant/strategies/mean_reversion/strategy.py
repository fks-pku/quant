"""RSI-based mean reversion on 1-minute data.

Educational example strategy.
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING
import pandas as pd

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("MeanReversion1m")
class MeanReversion1m(Strategy):
    """
    RSI-based mean reversion strategy on 1-minute data.
    Buy when oversold, sell when overbought.
    Educational example - not for live trading.
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        super().__init__("MeanReversion1m")
        self._symbols = symbols or ["SPY", "QQQ", "AAPL"]
        self._bars: Dict[str, List] = {}
        self._position_state: Dict[str, str] = {}

    @property
    def symbols(self) -> List[str]:
        """List of symbols this strategy trades."""
        return self._symbols

    @property
    def rsi_period(self) -> int:
        return 14

    @property
    def oversold_threshold(self) -> float:
        return 30.0

    @property
    def overbought_threshold(self) -> float:
        return 70.0

    def on_start(self, context: "Context") -> None:
        """Initialize strategy."""
        super().on_start(context)
        self.logger = get_logger("MeanReversion1m")
        for symbol in self._symbols:
            self._bars[symbol] = []
            self._position_state[symbol] = "flat"

    def on_data(self, context: "Context", data) -> None:
        """Process incoming 1-minute bars."""
        if not hasattr(data, "symbol"):
            return

        symbol = data.symbol
        if symbol not in self._symbols:
            return

        self._bars[symbol].append(data)

        if len(self._bars[symbol]) < self.rsi_period + 1:
            return

        rsi = self._calculate_rsi(symbol)
        if rsi is None:
            return

        current_price = data.close if hasattr(data, "close") else data["close"]
        position = context.portfolio.get_position(symbol)

        if rsi < self.oversold_threshold and self._position_state[symbol] == "flat":
            quantity = self._calculate_position_size(context, current_price)
            if quantity > 0:
                self.buy(symbol, quantity)
                self._position_state[symbol] = "long"

        elif rsi > self.overbought_threshold and self._position_state[symbol] == "long":
            if position and position.quantity > 0:
                self.sell(symbol, position.quantity)
                self._position_state[symbol] = "flat"

    def _calculate_rsi(self, symbol: str) -> Optional[float]:
        """Calculate RSI indicator."""
        bars = self._bars[symbol]
        if len(bars) < self.rsi_period + 1:
            return None

        prices = []
        for bar in bars[-self.rsi_period - 1:]:
            if hasattr(bar, "close"):
                prices.append(bar.close)
            elif isinstance(bar, dict):
                prices.append(bar.get("close", 0))
            else:
                return None

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[-self.rsi_period:]) / self.rsi_period
        avg_loss = sum(losses[-self.rsi_period:]) / self.rsi_period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_position_size(self, context: "Context", price: float) -> int:
        """Calculate position size based on risk parameters."""
        nav = context.portfolio.nav
        max_position_pct = 0.05
        position_value = nav * max_position_pct
        quantity = int(position_value / price)
        return max(1, quantity)

    def on_fill(self, context: "Context", fill: Any) -> None:
        """Track filled orders."""
        super().on_fill(context, fill)
        if hasattr(fill, "symbol"):
            self.logger.info(f"MeanReversion1m filled: {fill.side} {fill.quantity} {fill.symbol}")

    def on_stop(self, context: "Context") -> None:
        """Cleanup on strategy stop."""
        self._bars.clear()
        self._position_state.clear()
