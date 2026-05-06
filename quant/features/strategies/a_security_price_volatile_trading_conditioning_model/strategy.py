"""A Security Price Volatile Trading Conditioning Model

Source: arxiv (http://arxiv.org/abs/1001.0656v2)
Authors: Leilei Shi
Type: volatility
Summary: Detected: none
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("ASecurityPriceVolatileTradingConditioningModelStrategy")
class ASecurityPriceVolatileTradingConditioningModelStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="ASecurityPriceVolatileTradingConditioningModelStrategy")
        self._symbols = symbols or ["AAPL", "MSFT"]

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
