"""Learning to Learn Financial Networks for Optimising Momentum Strategies

Source: arxiv (http://arxiv.org/abs/2308.12212v1)
Authors: Xingyue Pu
Type: momentum
Summary: Promising network momentum strategy using end-to-end learning, but complexity and data needs may challenge daily-bar adaptation.
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("LearningToLearnFinancialNetworksForOptimisingMomentumStrategiesStrategy")
class LearningToLearnFinancialNetworksForOptimisingMomentumStrategiesStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="LearningToLearnFinancialNetworksForOptimisingMomentumStrategiesStrategy")
        self._symbols = symbols or ["ES", "NQ", "CL", "GC", "6E"]

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
