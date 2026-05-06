import numpy as np
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_cn_bars,
    make_us_bars,
    run_simple_backtest,
)
from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.dual_ma_crossover.strategy import DualMACrossover


START = __import__("datetime").datetime(2025, 1, 2)


class TestStrategyRegistry:
    def test_registered_strategies(self):
        assert StrategyRegistry.is_registered("DualMACrossover")

    def test_list_strategies(self):
        names = StrategyRegistry.list_strategies()
        assert "DualMACrossover" in names

    def test_create_strategy(self):
        s = StrategyRegistry.create("DualMACrossover", symbols=["AAPL"])
        assert isinstance(s, DualMACrossover)
        assert s.symbols == ["AAPL"]

    def test_case_insensitive(self):
        assert StrategyRegistry.is_registered("DualMACrossover")
        assert StrategyRegistry.is_registered("dualmacrossover")
        assert StrategyRegistry.is_registered("DUALMACROSSOVER")
        assert not StrategyRegistry.is_registered("nonexistent_strategy")


class TestStrategyBase:
    def test_on_fill_buy_accumulates(self):
        s = DualMACrossover(symbols=["AAPL"])

        class FakeFill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, FakeFill())
        assert s.get_position("AAPL") == 100

    def test_on_fill_sell_reduces(self):
        s = DualMACrossover(symbols=["AAPL"])

        class FakeFill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, FakeFill())

        class FakeFillSell:
            symbol = "AAPL"
            quantity = 50
            side = "SELL"

        s.on_fill(None, FakeFillSell())
        assert s.get_position("AAPL") == 50

    def test_on_fill_new_symbol_starts_zero(self):
        s = DualMACrossover(symbols=["AAPL"])

        class FakeFill:
            symbol = "MSFT"
            quantity = 200
            side = "BUY"

        s.on_fill(None, FakeFill())
        assert s.get_position("MSFT") == 200


class TestDualMACrossoverStrategy:
    def test_init_defaults(self):
        s = DualMACrossover()
        assert len(s.symbols) > 0
        assert s.fast_period == 5
        assert s.slow_period == 20

    def test_on_data_accumulates(self):
        s = DualMACrossover(symbols=["AAPL"])
        s.on_data(None, {"symbol": "AAPL", "close": 150})
        s.on_data(None, {"symbol": "AAPL", "close": 152})
        assert len(s._day_data["AAPL"]) == 2

    def test_get_last_price(self):
        s = DualMACrossover(symbols=["AAPL"])
        s._day_data["AAPL"] = [{"close": 150}, {"close": 155}]
        assert s._get_last_price("AAPL") == 155.0

    def test_get_last_price_no_data(self):
        s = DualMACrossover(symbols=["AAPL"])
        assert s._get_last_price("AAPL") == 0.0

    def test_dual_ma_backtest_runs(self):
        np.random.seed(42)
        data = make_us_bars(
            ["AAPL", "MSFT", "GOOGL"], START, 80,
            {"AAPL": 150, "MSFT": 400, "GOOGL": 140},
        )
        bt = make_backtester()
        s = DualMACrossover(symbols=["AAPL", "MSFT", "GOOGL"])
        result = run_simple_backtest(bt, data, [s], ["AAPL", "MSFT", "GOOGL"], initial_cash=1000000)
        assert result.final_nav > 0
