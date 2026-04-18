"""Tencent SMA Crossover Momentum Strategy.

Single-stock SMA crossover strategy for HK equities (e.g. HK.00700).
Goes long when fast SMA crosses above slow SMA, exits when it crosses below.

Hypothesis: Trend-following via moving average crossover captures persistent
momentum in large-cap HK equities with sufficient holding period to avoid
excessive turnover.

Author: Quantitative Research
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("TencentMomentum")
class TencentMomentum(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        fast_period: int = 20,
        slow_period: int = 60,
        position_pct: float = 0.95,
        atr_period: int = 14,
    ):
        super().__init__("TencentMomentum")
        self._symbols = symbols or ["HK.00700"]
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_pct = position_pct
        self.atr_period = atr_period
        self._bars: List[Dict] = []
        self._has_position = False

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("TencentMomentum")
        self.logger.info(
            f"TencentMomentum starting with fast={self.fast_period}, slow={self.slow_period}"
        )

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
        else:
            return
        if symbol not in self._symbols:
            return
        self._bars.append(data)

    def _compute_sma(self, period: int) -> Optional[float]:
        if len(self._bars) < period:
            return None
        closes = [b.get("close", 0) for b in self._bars[-period:]]
        return sum(closes) / len(closes)

    def _get_last_price(self) -> float:
        if self._bars:
            return float(self._bars[-1].get("close", 0))
        return 0.0

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        pass

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        fast_sma = self._compute_sma(self.fast_period)
        slow_sma = self._compute_sma(self.slow_period)

        if fast_sma is None or slow_sma is None:
            return

        symbol = self._symbols[0]
        price = self._get_last_price()
        if price <= 0:
            return

        if fast_sma > slow_sma and not self._has_position:
            nav = context.portfolio.nav
            quantity = int((nav * self.position_pct) / price)
            if quantity > 0:
                self.buy(symbol, quantity)
                self._has_position = True
                self.logger.info(
                    f"GOLDEN CROSS BUY: fast={fast_sma:.2f} > slow={slow_sma:.2f}, "
                    f"qty={quantity}, price={price:.2f}"
                )
        elif fast_sma < slow_sma and self._has_position:
            pos = context.portfolio.get_position(symbol)
            if pos and pos.quantity > 0:
                self.sell(symbol, int(pos.quantity))
                self._has_position = False
                self.logger.info(
                    f"DEATH CROSS SELL: fast={fast_sma:.2f} < slow={slow_sma:.2f}, "
                    f"qty={int(pos.quantity)}, price={price:.2f}"
                )

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)

    def on_stop(self, context: "Context") -> None:
        for symbol in self._symbols:
            pos = context.portfolio.get_position(symbol)
            if pos and pos.quantity > 0:
                price = self._get_last_price()
                self.sell(symbol, int(pos.quantity), "MARKET", price if price > 0 else None)
        self._bars.clear()
        self._has_position = False

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "has_position": self._has_position,
            "bar_count": len(self._bars),
            "parameters": {
                "fast_period": self.fast_period,
                "slow_period": self.slow_period,
                "position_pct": self.position_pct,
            },
        }
