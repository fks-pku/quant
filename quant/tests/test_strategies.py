"""Unit tests for strategies."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from quant.features.strategies.base import Strategy
from quant.features.strategies.framework import SignalGenerator, PortfolioConstructor, Signal


class MockStrategy(Strategy):
    """Mock strategy for testing."""

    def __init__(self):
        super().__init__("MockStrategy")
        self.on_start_called = False

    def on_start(self, context):
        super().on_start(context)
        self.on_start_called = True


class TestStrategy:
    """Tests for base Strategy class."""

    def test_strategy_initialization(self):
        """Test strategy initialization."""
        strategy = MockStrategy()

        assert strategy.name == "MockStrategy"
        assert strategy.context is None

    def test_on_start(self):
        """Test on_start callback."""
        strategy = MockStrategy()
        context = MagicMock()

        strategy.on_start(context)

        assert strategy.on_start_called is True
        assert strategy.context == context

    def test_buy_sell_helpers(self):
        """Test buy and sell helper methods."""
        strategy = MockStrategy()
        strategy.context = MagicMock()
        strategy.context.portfolio = MagicMock()

        strategy.buy("AAPL", 10)
        strategy.sell("AAPL", 5)


class TestSignalGenerator:
    """Tests for SignalGenerator."""

    def test_initialization(self):
        """Test signal generator initialization."""
        sg = SignalGenerator("test")

        assert sg.name == "test"
        assert len(sg.factors) == 0

    def test_add_factor(self):
        """Test adding a factor."""
        sg = SignalGenerator("test")

        def momentum(data):
            return 1.0

        sg.add_factor("momentum", momentum, weight=2.0)

        assert "momentum" in sg.factors
        assert sg.weights["momentum"] == 2.0

    def test_generate_signal(self):
        """Test generating a signal."""
        import pandas as pd

        sg = SignalGenerator("test")

        def momentum(data):
            return 1.0

        sg.add_factor("momentum", momentum, weight=1.0)

        data = pd.DataFrame({"close": [100, 105, 110]})
        signal = sg.generate_signal("AAPL", data)

        assert isinstance(signal, Signal)
        assert signal.symbol == "AAPL"
        assert signal.score > 0


class TestPortfolioConstructor:
    """Tests for PortfolioConstructor."""

    def test_equal_weight(self):
        """Test equal weight portfolio construction."""
        import pandas as pd

        pc = PortfolioConstructor(method="equal_weight")

        signals = [
            Signal(symbol="AAPL", score=1.0, timestamp=datetime.now(), factors={}, direction="long"),
            Signal(symbol="GOOGL", score=0.5, timestamp=datetime.now(), factors={}, direction="long"),
            Signal(symbol="MSFT", score=-1.0, timestamp=datetime.now(), factors={}, direction="short"),
        ]

        prices = {"AAPL": 150, "GOOGL": 140, "MSFT": 380}
        nav = 100000

        positions = pc.construct(signals, prices, nav, max_position_pct=0.1)

        assert "AAPL" in positions
        assert "GOOGL" in positions


class TestSignal:
    """Tests for Signal dataclass."""

    def test_signal_creation(self):
        """Test creating a signal."""
        signal = Signal(
            symbol="AAPL",
            score=1.0,
            timestamp=datetime.now(),
            factors={"momentum": 0.8, "mean_reversion": 0.2},
            direction="long",
        )

        assert signal.symbol == "AAPL"
        assert signal.score == 1.0
        assert signal.direction == "long"
        assert len(signal.factors) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
