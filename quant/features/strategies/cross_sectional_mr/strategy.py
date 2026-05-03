"""Cross-Sectional Mean Reversion Strategy.

Stocks that have underperformed the market over the past N days revert to mean.
Long the most underperforming, short the most overperforming, equal weight per leg.
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("CrossSectionalMeanReversion")
class CrossSectionalMeanReversion(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        market_symbol: str = "SPY",
        lookback_days: int = 5,
        holding_days: int = 5,
        top_pct: float = 0.1,
        bottom_pct: float = 0.1,
        max_position_pct: float = 0.05,
    ):
        super().__init__("CrossSectionalMeanReversion",
                         symbols or _DEFAULT_SYMBOLS, holding_days)
        self.market_symbol = market_symbol
        self.lookback_days = lookback_days
        self.top_pct = top_pct
        self.bottom_pct = bottom_pct
        self.max_position_pct = max_position_pct

        self._long_positions: List[str] = []
        self._short_positions: List[str] = []
        self._excess_returns: Dict[str, float] = {}

    @property
    def _max_keep_hint(self) -> int:
        return 0  # no trim — need full history for lookback

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("CrossSectionalMeanReversion")
        self.logger.info(
            f"CrossSectionalMeanReversion starting with lookback={self.lookback_days}, "
            f"holding_days={self.holding_days}"
        )

    def _calculate_excess_returns(self) -> None:
        self._excess_returns.clear()
        market_closes = self._get_closes(self.market_symbol)
        if len(market_closes) < self.lookback_days + 1:
            return
        market_ret = (market_closes[-1] / market_closes[-self.lookback_days - 1]) - 1
        for symbol in self._symbols:
            closes = self._get_closes(symbol)
            if len(closes) >= self.lookback_days + 1:
                stock_ret = (closes[-1] / closes[-self.lookback_days - 1]) - 1
                self._excess_returns[symbol] = stock_ret - market_ret
            else:
                self._excess_returns[symbol] = 0.0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        self._calculate_excess_returns()
        if not self._excess_returns:
            return

        sorted_by_excess = sorted(self._excess_returns.items(), key=lambda x: x[1])
        n_stocks = len(sorted_by_excess)
        n_long = max(1, int(n_stocks * self.bottom_pct))
        n_short = max(1, int(n_stocks * self.top_pct))

        new_long = [s[0] for s in sorted_by_excess[:n_long]]
        new_short = [s[0] for s in sorted_by_excess[-n_short:]]

        for sym in list(self._long_positions):
            if sym not in new_long:
                pos_qty = self._positions.get(sym, 0)
                if pos_qty > 0:
                    self.sell(sym, pos_qty)
        for sym in list(self._short_positions):
            if sym not in new_short:
                pos_qty = self._positions.get(sym, 0)
                if pos_qty > 0:
                    self.sell(sym, pos_qty)

        self._long_positions = new_long
        self._short_positions = new_short

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

        self.logger.info(
            f"CrossSectionalMeanReversion rebalanced: long={self._long_positions}, "
            f"short={self._short_positions}"
        )

    def _on_stop_cleanup(self) -> None:
        self._long_positions.clear()
        self._short_positions.clear()
        self._excess_returns.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "market_symbol": self.market_symbol,
            "lookback_days": self.lookback_days,
            "holding_days": self.holding_days,
            "top_pct": self.top_pct,
            "bottom_pct": self.bottom_pct,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "long_positions": self._long_positions,
            "short_positions": self._short_positions,
            "excess_returns": self._excess_returns,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
        }


_DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
