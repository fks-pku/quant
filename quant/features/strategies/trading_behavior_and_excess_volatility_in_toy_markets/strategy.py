"""Trading behavior and excess volatility in toy markets

Source: arxiv (http://arxiv.org/abs/cond-mat/0004376v2)
Authors: M. Marsili
Type: volatility
Summary: Detected: none
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("TradingBehaviorAndExcessVolatilityInToyMarketsStrategy")
class TradingBehaviorAndExcessVolatilityInToyMarketsStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="TradingBehaviorAndExcessVolatilityInToyMarketsStrategy")
        self._symbols = symbols or ["SPY"]

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_data(self, context: Any, data: Any) -> None:
        # TODO: Implement volatility logic based on paper
        pass

    def on_before_trading(self, context: Any, trading_date: Any) -> None:
        pass

    def on_after_trading(self, context: Any, trading_date: Any) -> None:
        pass
