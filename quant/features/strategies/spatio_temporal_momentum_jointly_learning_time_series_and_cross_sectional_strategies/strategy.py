"""Spatio-Temporal Momentum: Jointly Learning Time-Series and Cross-Sectional Strategies

Source: arxiv (http://arxiv.org/abs/2302.10175v1)
Authors: Wee Ling Tan
Type: momentum
Summary: Detected: momentum
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("SpatiotemporalMomentumJointlyLearningTimeseriesAndCrosssectionalStrategiesStrategy")
class SpatiotemporalMomentumJointlyLearningTimeseriesAndCrosssectionalStrategiesStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="SpatiotemporalMomentumJointlyLearningTimeseriesAndCrosssectionalStrategiesStrategy")
        self._symbols = symbols or ["SPY", "QQQ"]

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
