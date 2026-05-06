"""Follow the Leader: Enhancing Systematic Trend-Following Using Network Momentum

Source: arxiv (http://arxiv.org/abs/2501.07135v1)
Authors: Linze Li
Type: momentum
Summary: A trend-following strategy enhanced with network momentum from lead-lag relationships, suitable for daily bars on liquid futures.
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy")
class FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy")
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
