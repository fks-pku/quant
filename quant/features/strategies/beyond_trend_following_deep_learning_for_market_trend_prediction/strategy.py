"""Beyond Trend Following: Deep Learning for Market Trend Prediction

Source: arxiv (http://arxiv.org/abs/2407.13685v1)
Authors: Fernando Berzal
Type: momentum
Summary: Detected: momentum
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("BeyondTrendFollowingDeepLearningForMarketTrendPredictionStrategy")
class BeyondTrendFollowingDeepLearningForMarketTrendPredictionStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="BeyondTrendFollowingDeepLearningForMarketTrendPredictionStrategy")
        self._symbols = symbols or ["SPY"]

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_data(self, context: Any, data: Any) -> None:
        # TODO: Implement momentum logic based on paper
        pass

    def on_before_trading(self, context: Any, trading_date: Any) -> None:
        pass

    def on_after_trading(self, context: Any, trading_date: Any) -> None:
        pass
