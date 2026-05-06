"""Power Assisted Trend Following

Source: arxiv (http://arxiv.org/abs/2003.09298v1)
Authors: Andreas A. Aigner
Type: momentum
Summary: A daily-bar trend following strategy that applies digital signal processing to filter market noise from directional trends, leveraging concepts related to Welles Wilder's volatility and directional movement indicators.
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("PowerAssistedTrendFollowingStrategy")
class PowerAssistedTrendFollowingStrategy(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="PowerAssistedTrendFollowingStrategy")
        self._symbols = symbols or ["SPY", "GLD", "TLT"]

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
