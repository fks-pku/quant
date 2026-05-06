"""Enhancing Time Series Momentum Strategies Using Deep Neural Networks

Source: arxiv (http://arxiv.org/abs/1904.04912v3)
Authors: Bryan Lim
Type: momentum
Summary: Deep Momentum Networks use LSTM-based deep learning to simultaneously learn trend estimation and position sizing within a volatility-scaling framework, achieving significant improvements over traditional time series momentum on 88 futures contracts, but require substantial deep learning expertise and carry meaningful overfitting risk.
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("EnhancingTimeSeriesMomentumStrategiesUsingDeepNeuralNetworksStrategy")
class EnhancingTimeSeriesMomentumStrategiesUsingDeepNeuralNetworksStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="EnhancingTimeSeriesMomentumStrategiesUsingDeepNeuralNetworksStrategy")
        self._symbols = symbols or ["ES", "NQ", "CL", "GC", "TY", "ZN", "SPY", "TLT", "GLD", "USO"]

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
