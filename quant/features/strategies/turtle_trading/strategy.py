"""Simplified Turtle Trading Strategy - Donchian Channel breakout for A-shares.

Entry: Buy when close breaks above the highest high of the past N days (default 20).
Exit:  Sell when close breaks below the lowest low of the past M days (default 10).
Position sizing: Fixed percentage of NAV per symbol.

This is the classic Richard Dennis / William Eckhardt system stripped down
for daily-bar backtesting on A-share markets. The original system also
includes ATR-based position sizing and pyramiding, which are omitted here
in favor of simplicity.

Hypothesis: Prices trending to new highs/lows over a lookback window tend
to persist, capturing momentum from institutional flow and news catalysts.
The Donchian channel provides a clear, rule-based entry/exit framework.

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.features.trading.engine import Context


@strategy("TurtleTrading")
class TurtleTrading(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        entry_period: int = 20,
        exit_period: int = 10,
        max_position_pct: float = 0.05,
        atr_period: int = 20,
    ):
        super().__init__("TurtleTrading")
        self._symbols = symbols or ["600519", "000858", "601318"]
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.max_position_pct = max_position_pct
        self.atr_period = atr_period

        self._day_data: Dict[str, List] = {}
        self._entry_prices: Dict[str, float] = {}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("TurtleTrading")
        self.logger.info(
            f"TurtleTrading starting with entry={self.entry_period}, "
            f"exit={self.exit_period}, max_pos_pct={self.max_position_pct}"
        )

    def _get_bars(self, symbol: str) -> List:
        return self._day_data.get(symbol, [])

    def _get_last_price(self, symbol: str) -> float:
        bars = self._get_bars(symbol)
        if not bars:
            return 0.0
        last = bars[-1]
        if isinstance(last, dict):
            return float(last.get("close", 0))
        return float(getattr(last, "close", 0))

    def _donchian_high(self, symbol: str, period: int) -> float:
        bars = self._get_bars(symbol)
        if len(bars) < period + 1:
            return np.inf
        highs = [self._adj(bars[i], "high") for i in range(-period - 1, -1)]
        return max(highs) if highs else np.inf

    def _donchian_low(self, symbol: str, period: int) -> float:
        bars = self._get_bars(symbol)
        if len(bars) < period + 1:
            return -np.inf
        lows = [self._adj(bars[i], "low") for i in range(-period - 1, -1)]
        return min(lows) if lows else -np.inf

    def _calculate_atr(self, symbol: str) -> float:
        bars = self._get_bars(symbol)
        if len(bars) < self.atr_period + 1:
            return 0.0
        trs = []
        for i in range(-self.atr_period, 0):
            h = self._adj(bars[i], "high")
            l = self._adj(bars[i], "low")
            pc = self._adj(bars[i - 1], "close")
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return float(np.mean(trs)) if trs else 0.0

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
        else:
            return

        if not symbol or symbol not in self._symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []
        self._day_data[symbol].append(data)

        max_keep = (max(self.entry_period, self.atr_period) + 1) * 3
        if len(self._day_data[symbol]) > max_keep:
            self._day_data[symbol] = self._day_data[symbol][-max_keep:]

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        nav = context.portfolio.nav

        for symbol in self._symbols:
            bars = self._get_bars(symbol)
            if len(bars) < self.entry_period + 1:
                continue

            close = self._adj(bars[-1], "close")
            if close <= 0:
                continue

            pos_qty = self._positions.get(symbol, 0)

            if pos_qty > 0:
                exit_low = self._donchian_low(symbol, self.exit_period)
                if close < exit_low:
                    self.sell(symbol, pos_qty)
                    self._entry_prices.pop(symbol, None)
                    self.logger.info(
                        f"TurtleTrading exit {symbol}: close={close:.2f} "
                        f"below exit low={exit_low:.2f}"
                    )
            else:
                entry_high = self._donchian_high(symbol, self.entry_period)
                if close > entry_high:
                    price = self._get_last_price(symbol)
                    if price <= 0:
                        continue
                    qty = int((nav * self.max_position_pct) / price)
                    if qty > 0:
                        self.buy(symbol, qty)
                        self._entry_prices[symbol] = close
                        atr = self._calculate_atr(symbol)
                        self.logger.info(
                            f"TurtleTrading entry {symbol}: close={close:.2f} "
                            f"above entry high={entry_high:.2f}, ATR={atr:.2f}"
                        )

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
        self._day_data.clear()
        self._entry_prices.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entry_prices": self._entry_prices,
            "parameters": {
                "entry_period": self.entry_period,
                "exit_period": self.exit_period,
                "max_position_pct": self.max_position_pct,
                "atr_period": self.atr_period,
                "symbols": self._symbols,
            },
        }
