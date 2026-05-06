"""AI for trading strategies

Source: arxiv (http://arxiv.org/abs/2208.07168v1)
Authors: Danijel Jevtic
Type: volatility
Summary: Detected: none
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("AiForTradingStrategiesStrategy")
class AiForTradingStrategiesStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="AiForTradingStrategiesStrategy")
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
