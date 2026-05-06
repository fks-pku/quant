"""Learning to Learn Financial Networks for Optimising Momentum Strategies

Source: arxiv (http://arxiv.org/abs/2308.12212v1)
Authors: Xingyue Pu
Type: momentum
Summary: L2GMOM uses complex end-to-end deep learning via algorithm unrolling to simultaneously learn financial networks and optimize network momentum portfolios, offering strong theoretical performance but facing significant practical implementation hurdles.
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("LearningToLearnFinancialNetworksForOptimisingMomentumStrategiesStrategy")
class LearningToLearnFinancialNetworksForOptimisingMomentumStrategiesStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="LearningToLearnFinancialNetworksForOptimisingMomentumStrategiesStrategy")
        self._symbols = symbols or ["ES", "CL", "GC", "ZN"]

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
