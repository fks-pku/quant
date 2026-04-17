"""Dual Thrust breakout system adapted for futures.

Reference implementation.
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING
import pandas as pd

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("DualThrust")
class DualThrust(Strategy):
    """
    Dual Thrust breakout system.
    Classic range breakout strategy adapted for futures.
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        super().__init__("DualThrust")
        self._symbols = symbols or ["ES", "NQ", "YM"]
        self._bars: Dict[str, List] = {}
        self._position_state: Dict[str, str] = {}

    @property
    def symbols(self) -> List[str]:
        """List of symbols this strategy trades."""
        return self._symbols

    @property
    def lookback_period(self) -> int:
        return 5

    @property
    def k_value(self) -> float:
        return 0.5

    def on_start(self, context: "Context") -> None:
        """Initialize strategy."""
        super().on_start(context)
        self.logger = get_logger("DualThrust")
        for symbol in self._symbols:
            self._bars[symbol] = []
            self._position_state[symbol] = "flat"

    def on_data(self, context: "Context", data) -> None:
        """Process incoming daily bars."""
        if not hasattr(data, "symbol"):
            return

        symbol = data.symbol
        if symbol not in self._symbols:
            return

        self._bars[symbol].append(data)

        if len(self._bars[symbol]) < self.lookback_period + 1:
            return

        signals = self._calculate_signals(symbol)
        if signals is None:
            return

        long_entry = signals["long_entry"]
        short_entry = signals["short_entry"]
        current_price = data.close if hasattr(data, "close") else data["close"]

        position = context.portfolio.get_position(symbol)

        if current_price > long_entry and self._position_state[symbol] == "flat":
            quantity = self._calculate_position_size(context, current_price)
            if quantity > 0:
                self.buy(symbol, quantity)
                self._position_state[symbol] = "long"

        elif current_price < short_entry and self._position_state[symbol] == "flat":
            quantity = self._calculate_position_size(context, current_price)
            if quantity > 0:
                self.sell(symbol, quantity)
                self._position_state[symbol] = "short"

        elif self._position_state[symbol] == "long" and current_price < signals["exit"]:
            if position and position.quantity > 0:
                self.sell(symbol, position.quantity)
                self._position_state[symbol] = "flat"

        elif self._position_state[symbol] == "short" and current_price > signals["exit"]:
            if position and position.quantity > 0:
                self.buy(symbol, position.quantity)
                self._position_state[symbol] = "flat"

    def _calculate_signals(self, symbol: str) -> Optional[Dict]:
        """Calculate Dual Thrust entry and exit levels."""
        bars = self._bars[symbol][-self.lookback_period:]

        if len(bars) < self.lookback_period:
            return None

        hh = max(bar.high if hasattr(bar, "high") else bar.get("high", 0) for bar in bars)
        ll = min(bar.low if hasattr(bar, "low") else bar.get("low", 0) for bar in bars)
        hc = max(bar.close if hasattr(bar, "close") else bar.get("close", 0) for bar in bars)
        lc = min(bar.close if hasattr(bar, "close") else bar.get("close", 0) for bar in bars)

        range_val = max(hh - lc, hc - ll)

        open_price = bars[-1].open if hasattr(bars[-1], "open") else bars[-1].get("open", 0)

        long_entry = open_price + self.k_value * range_val
        short_entry = open_price - self.k_value * range_val
        exit_level = open_price

        return {
            "long_entry": long_entry,
            "short_entry": short_entry,
            "exit": exit_level,
        }

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
            self.logger.info(f"DualThrust filled: {fill.side} {fill.quantity} {fill.symbol}")

    def on_stop(self, context: "Context") -> None:
        """Cleanup on strategy stop."""
        self._bars.clear()
        self._position_state.clear()
