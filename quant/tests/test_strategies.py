from pathlib import Path

from quant.features.strategies.base import Strategy
from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import StrategyRegistry, strategy


class TestStrategyRegistry:
    def test_generated_strategy_directories_have_required_files(self):
        strategies_dir = Path(__file__).resolve().parents[1] / "features" / "strategies"
        strategy_dirs = [
            p for p in strategies_dir.iterdir()
            if p.is_dir() and (p / "strategy.py").exists()
        ]
        assert strategy_dirs
        assert all((p / "config.yaml").exists() for p in strategy_dirs)
        assert all((p / "README.md").exists() for p in strategy_dirs)

    def test_register_and_create_strategy(self):
        @strategy("FrameworkSmoke")
        class FrameworkSmoke(Strategy):
            def __init__(self, symbols=None):
                super().__init__("FrameworkSmoke")
                self._symbols = symbols or []

            @property
            def symbols(self):
                return self._symbols

        s = StrategyRegistry.create("FrameworkSmoke", symbols=["AAPL"])
        assert isinstance(s, FrameworkSmoke)
        assert s.symbols == ["AAPL"]

    def test_case_insensitive(self):
        @strategy("CaseSmoke")
        class CaseSmoke(Strategy):
            def __init__(self):
                super().__init__("CaseSmoke")

        assert StrategyRegistry.is_registered("CaseSmoke")
        assert StrategyRegistry.is_registered("casesmoke")
        assert StrategyRegistry.is_registered("CASESMOKE")
        assert not StrategyRegistry.is_registered("nonexistent_strategy")


class TestStrategyBase:
    def test_on_fill_buy_accumulates(self):
        s = _BaseSmokeStrategy()

        class FakeFill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, FakeFill())
        assert s.get_position("AAPL") == 100

    def test_on_fill_sell_reduces(self):
        s = _BaseSmokeStrategy()

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
        s = _BaseSmokeStrategy()

        class FakeFill:
            symbol = "MSFT"
            quantity = 200
            side = "BUY"

        s.on_fill(None, FakeFill())
        assert s.get_position("MSFT") == 200


class TestDailyBarStrategy:
    def test_on_data_accumulates(self):
        s = _DailySmokeStrategy(symbols=["AAPL"])
        s.on_data(None, {"symbol": "AAPL", "close": 150})
        s.on_data(None, {"symbol": "AAPL", "close": 152})
        assert len(s._day_data["AAPL"]) == 2

    def test_ignores_untracked_symbols(self):
        s = _DailySmokeStrategy(symbols=["AAPL"])
        s.on_data(None, {"symbol": "MSFT", "close": 300})
        assert "MSFT" not in s._day_data

    def test_get_last_price(self):
        s = _DailySmokeStrategy(symbols=["AAPL"])
        s._day_data["AAPL"] = [{"close": 150}, {"close": 155}]
        assert s._get_last_price("AAPL") == 155.0

    def test_get_last_price_no_data(self):
        s = _DailySmokeStrategy(symbols=["AAPL"])
        assert s._get_last_price("AAPL") == 0.0


class _BaseSmokeStrategy(Strategy):
    def __init__(self):
        super().__init__("BaseSmoke")


class _DailySmokeStrategy(DailyBarStrategy):
    def __init__(self, symbols=None):
        super().__init__("DailySmoke", symbols or ["AAPL"])
