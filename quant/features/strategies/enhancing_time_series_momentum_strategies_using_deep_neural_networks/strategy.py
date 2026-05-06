"""Enhancing Time Series Momentum Strategies Using Deep Neural Networks

Source: arxiv (http://arxiv.org/abs/1904.04912v3)
Authors: Bryan Lim
Type: momentum
Summary: Detected: momentum
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("EnhancingTimeSeriesMomentumStrategiesUsingDeepNeuralNetworksStrategy")
class EnhancingTimeSeriesMomentumStrategiesUsingDeepNeuralNetworksStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="EnhancingTimeSeriesMomentumStrategiesUsingDeepNeuralNetworksStrategy")
        self._symbols = symbols or ["ES", "NQ"]

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
