"""Invariant tests for strategies module — Registry, Strategy base, _adj."""
import math

import pytest

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import StrategyRegistry, strategy


# ---------------------------------------------------------------------------
# CASE-1: Registry CRUD
# ---------------------------------------------------------------------------

class TestCase1RegistryCRUD:
    def test_s1_01_registered(self):
        @strategy("TestInvS1")
        class Dummy:
            pass
        assert StrategyRegistry.is_registered("TestInvS1") is True

    def test_s1_02_create_instance(self):
        @strategy("TestInvS1b")
        class DummyB:
            def __init__(self, val=0):
                self.val = val
        inst = StrategyRegistry.create("TestInvS1b", val=42)
        assert isinstance(inst, DummyB)
        assert inst.val == 42

    def test_s1_03_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            StrategyRegistry.create("NonExistentStrategy999")

    def test_s1_04_list_contains(self):
        @strategy("TestInvS1c")
        class DummyC:
            pass
        assert "TestInvS1c" in StrategyRegistry.list_strategies()


# ---------------------------------------------------------------------------
# CASE-2: _adj helper priority
# ---------------------------------------------------------------------------

class TestCase2AdjHelper:
    """_adj() is for signals/indicators — returns backward-adjusted price as-is."""

    def test_s2_01_prefers_adj_close(self):
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._adj(bar, "close") == pytest.approx(105.0)

    def test_s2_02_falls_back_to_close(self):
        bar = {"close": 100.0}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_03_nan_fallback(self):
        bar = {"close": 100.0, "adj_close": float("nan")}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_04_none_fallback(self):
        bar = {"close": 100.0, "adj_close": None}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_05_cn_backward_adjusted_preserved_for_signals(self):
        """CN adj_close = close * adj_factor. _adj keeps it for MA continuity."""
        bar = {"close": 10.0, "adj_close": 1160.0, "adj_factor": 116.0}
        assert Strategy._adj(bar, "close") == pytest.approx(1160.0)


# ---------------------------------------------------------------------------
# CASE-2b: _price helper — real market price for quantity/order sizing
# ---------------------------------------------------------------------------

class TestCase2bPriceHelper:
    """_price() is for order sizing — returns actual market close."""

    def test_s2b_01_returns_close(self):
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._price(bar) == pytest.approx(100.0)

    def test_s2b_02_cn_real_price(self):
        """Even with high adj_close, _price returns actual market price."""
        bar = {"close": 10.0, "adj_close": 1160.0, "adj_factor": 116.0}
        assert Strategy._price(bar) == pytest.approx(10.0)

    def test_s2b_03_zero_close(self):
        bar = {"close": 0.0}
        assert Strategy._price(bar) == pytest.approx(0.0)

    def test_s2b_04_missing_close(self):
        bar = {}
        assert Strategy._price(bar) == pytest.approx(0.0)

    def test_s2b_05_object_bar(self):
        bar = type("Bar", (), {"close": 55.5, "adj_close": 999.0})()
        assert Strategy._price(bar) == pytest.approx(55.5)


# ---------------------------------------------------------------------------
# CASE-3: buy/sell no-context silent failure
# ---------------------------------------------------------------------------

class TestCase3NoContext:
    def test_s3_01_buy_returns_none(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_no_ctx")

        s = TestStrat()
        assert s.buy("AAPL", 100) is None

    def test_s3_02_sell_returns_none(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_no_ctx2")

        s = TestStrat()
        assert s.sell("AAPL", 100) is None


# ---------------------------------------------------------------------------
# CASE-4: on_fill updates internal positions
# ---------------------------------------------------------------------------

class TestCase4OnFill:
    def test_s4_01_buy_updates_position(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_fill1")

        s = TestStrat()

        class Fill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, Fill())
        assert s.get_position("AAPL") == 100

    def test_s4_02_sell_updates_position(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_fill2")

        s = TestStrat()

        class FillBuy:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        class FillSell:
            symbol = "AAPL"
            quantity = 40
            side = "SELL"

        s.on_fill(None, FillBuy())
        s.on_fill(None, FillSell())
        assert s.get_position("AAPL") == 60
