"""Continuous-time trading and emergence of volatility

Source: arxiv (http://arxiv.org/abs/0712.1483v2)
Authors: Vladimir Vovk
Type: volatility
Summary: Detected: none
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("ContinuoustimeTradingAndEmergenceOfVolatilityStrategy")
class ContinuoustimeTradingAndEmergenceOfVolatilityStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="ContinuoustimeTradingAndEmergenceOfVolatilityStrategy")
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
