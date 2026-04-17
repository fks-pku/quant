"""Dual Momentum Strategy.

Combines absolute momentum (trend following via SMA) with relative momentum
(cross-sectional ranking). Only takes trades when both agree.

Hypothesis: Dual confirmation filters false signals and improves risk-adjusted returns.
Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("DualMomentum")
class DualMomentum(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        abs_lookback: int = 60,
        rel_lookback: int = 20,
        sma_short: int = 20,
        holding_days: int = 21,
        top_tercile_pct: float = 0.33,
        bottom_tercile_pct: float = 0.33,
        max_position_pct: float = 0.05,
    ):
        super().__init__("DualMomentum")
        self._symbols = symbols or [
            "SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META"
        ]
        self.abs_lookback = abs_lookback
        self.rel_lookback = rel_lookback
        self.sma_short = sma_short
        self.holding_days = holding_days
        self.top_tercile_pct = top_tercile_pct
        self.bottom_tercile_pct = bottom_tercile_pct
        self.max_position_pct = max_position_pct

        self._day_data: Dict[str, List] = {}
        self._last_rebalance_date: Optional[date] = None
        self._days_since_rebalance: int = 0
        self._current_positions: Dict[str, float] = {}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("DualMomentum")
        self.logger.info(
            f"DualMomentum starting with abs_lookback={self.abs_lookback}, "
            f"rel_lookback={self.rel_lookback}"
        )

    def _get_closes(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        closes = []
        for bar in bars:
            if isinstance(bar, dict):
                closes.append(bar.get("close", 0))
            elif hasattr(bar, "close"):
                closes.append(bar.close)
            else:
                closes.append(0)
        return closes

    def _get_last_price(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        return float(closes[-1]) if closes else 0.0

    def _compute_sma(self, closes: List[float], period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        return float(np.mean(closes[-period:]))

    def _compute_returns(self) -> Dict[str, float]:
        returns = {}
        for symbol in self._symbols:
            closes = self._get_closes(symbol)
            if len(closes) >= self.rel_lookback + 1:
                ret = (closes[-1] / closes[-self.rel_lookback - 1]) - 1
                returns[symbol] = ret
            else:
                returns[symbol] = 0.0
        return returns

    def _rank_by_return(self, returns: Dict[str, float]) -> Dict[str, int]:
        sorted_syms = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        ranks = {}
        for i, (sym, _) in enumerate(sorted_syms):
            ranks[sym] = i + 1
        return ranks

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        returns = self._compute_returns()
        ranks = self._rank_by_return(returns)
        n_stocks = len(self._symbols)

        top_cutoff = max(1, int(n_stocks * self.top_tercile_pct))
        bottom_cutoff = n_stocks - max(1, int(n_stocks * self.bottom_tercile_pct))

        entries: List[str] = []
        exits: List[str] = []

        for symbol in self._symbols:
            closes = self._get_closes(symbol)
            sma_long = self._compute_sma(closes, self.abs_lookback)
            sma_short_val = self._compute_sma(closes, self.sma_short)
            current_price = closes[-1] if closes else 0

            if sma_long is None or sma_short_val is None or current_price <= 0:
                continue

            rank = ranks.get(symbol, n_stocks)

            bullish_trend = current_price > sma_long
            top_ranked = rank <= top_cutoff
            bearish_trend = current_price < sma_short_val
            bottom_ranked = rank > bottom_cutoff

            if bullish_trend and top_ranked:
                entries.append(symbol)
            elif bearish_trend or bottom_ranked:
                exits.append(symbol)

        for symbol in exits:
            pos = self._current_positions.get(symbol, 0)
            if pos > 0:
                price = self._get_last_price(symbol)
                if price > 0:
                    self.sell(symbol, int(pos))
                del self._current_positions[symbol]

        if entries:
            nav = context.portfolio.nav
            weight = self.max_position_pct / len(entries)
            for symbol in entries:
                price = self._get_last_price(symbol)
                if price > 0:
                    quantity = int((nav * weight) / price)
                    if quantity > 0:
                        self.buy(symbol, quantity)
                        self._current_positions[symbol] = quantity

        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

        self.logger.info(
            f"DualMomentum rebalanced: entries={entries}, exits={exits}"
        )

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

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        pass

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        if self._last_rebalance_date is not None:
            self._days_since_rebalance += 1
            if self._days_since_rebalance < self.holding_days:
                return
        self._execute_rebalance(context, trading_date)

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
        self._day_data.clear()
        self._current_positions.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "current_positions": dict(self._current_positions),
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
            "parameters": {
                "abs_lookback": self.abs_lookback,
                "rel_lookback": self.rel_lookback,
                "sma_short": self.sma_short,
                "holding_days": self.holding_days,
                "top_tercile_pct": self.top_tercile_pct,
                "bottom_tercile_pct": self.bottom_tercile_pct,
                "max_position_pct": self.max_position_pct,
            },
        }
