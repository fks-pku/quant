"""策略测试 — 所有注册策略的基本验证。"""
from datetime import datetime, date

import numpy as np
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_cn_bars,
    make_us_bars,
    run_simple_backtest,
)
from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.simple_momentum.strategy import SimpleMomentum
from quant.features.strategies.daily_return_anomaly.strategy import DailyReturnAnomaly
from quant.features.strategies.regime_filtered_momentum.strategy import RegimeFilteredMomentum
from quant.features.strategies.volatility_scaled_trend.strategy import VolatilityScaledTrend
from quant.features.strategies.volatility_regime.strategy import VolatilityRegime


START = datetime(2025, 1, 2)


class TestStrategyRegistry:
    def test_registered_strategies(self):
        assert StrategyRegistry.is_registered("SimpleMomentum")
        assert StrategyRegistry.is_registered("DailyReturnAnomaly")
        assert StrategyRegistry.is_registered("RegimeFilteredMomentum")
        assert StrategyRegistry.is_registered("VolatilityScaledTrend")

    def test_list_strategies(self):
        names = StrategyRegistry.list_strategies()
        assert "SimpleMomentum" in names
        assert "DailyReturnAnomaly" in names

    def test_create_strategy(self):
        s = StrategyRegistry.create("SimpleMomentum", symbols=["AAPL"])
        assert isinstance(s, SimpleMomentum)
        assert s.symbols == ["AAPL"]

    def test_case_insensitive(self):
        assert StrategyRegistry.is_registered("SimpleMomentum")
        assert not StrategyRegistry.is_registered("nonexistent_strategy")


class TestStrategyBase:
    def test_on_fill_buy_accumulates(self):
        s = SimpleMomentum(symbols=["AAPL"])

        class FakeFill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, FakeFill())
        assert s.get_position("AAPL") == 100

    def test_on_fill_sell_reduces(self):
        s = SimpleMomentum(symbols=["AAPL"])

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
        s = SimpleMomentum(symbols=["AAPL"])

        class FakeFill:
            symbol = "MSFT"
            quantity = 200
            side = "BUY"

        s.on_fill(None, FakeFill())
        assert s.get_position("MSFT") == 200


class TestDailyReturnAnomalyStrategy:
    def test_init_defaults(self):
        s = DailyReturnAnomaly()
        assert len(s.symbols) > 0
        assert s.streak_short == 3
        assert s.short_weight == 0.6

    def test_count_streak(self):
        s = DailyReturnAnomaly(symbols=["600519"])
        s._day_data["600519"] = [
            {"close": 100}, {"close": 101}, {"close": 102},
            {"close": 103}, {"close": 104},
        ]
        streak = s._count_streak("600519")
        assert streak == 4

    def test_count_streak_mixed(self):
        s = DailyReturnAnomaly(symbols=["600519"])
        s._day_data["600519"] = [
            {"close": 100}, {"close": 101}, {"close": 100}, {"close": 101},
        ]
        streak = s._count_streak("600519")
        assert streak == 1

    def test_short_term_signal_threshold(self):
        s = DailyReturnAnomaly(symbols=["600519"])
        assert s._calculate_short_term_signal(2) == 0.0
        assert s._calculate_short_term_signal(3) != 0.0

    def test_medium_term_signal_reversal(self):
        s = DailyReturnAnomaly(symbols=["600519"])
        bars = [{"close": 100.0}] * 10 + [{"close": 120.0}] + [{"close": 120.0}] * 10
        s._day_data["600519"] = bars
        signal = s._calculate_medium_term_signal("600519")
        assert signal < 0


class TestRegimeFilteredMomentumStrategy:
    def test_init_defaults(self):
        s = RegimeFilteredMomentum()
        assert s.benchmark_symbol == "510300"
        assert s.vol_low_threshold == 0.15
        assert s.vol_high_threshold == 0.25

    def test_momentum_calculation(self):
        s = RegimeFilteredMomentum(symbols=["600519"])
        s._day_data["600519"] = [
            {"close": 100}, {"close": 102}, {"close": 104},
            {"close": 106}, {"close": 108}, {"close": 110},
            {"close": 112}, {"close": 114}, {"close": 116},
            {"close": 118}, {"close": 120},
            {"close": 122}, {"close": 124}, {"close": 126},
            {"close": 128}, {"close": 130}, {"close": 132},
            {"close": 134}, {"close": 136}, {"close": 138},
            {"close": 140},
        ]
        mom = s._calculate_momentum("600519")
        assert mom > 0

    def test_regime_detection(self):
        s = RegimeFilteredMomentum(symbols=["600519"], benchmark_symbol="510300")
        closes = [100 + i * 0.1 for i in range(25)]
        s._day_data["510300"] = [{"close": c} for c in closes]
        regime, exposure = s._detect_regime()
        assert regime in ("low_vol", "transition", "high_vol", "normal")
        assert 0 <= exposure <= 1.0


class TestVolatilityScaledTrendStrategy:
    def test_init_defaults(self):
        s = VolatilityScaledTrend()
        assert s.sma_lookback == 50
        assert s.target_vol == 0.15

    def test_sma_calculation(self):
        s = VolatilityScaledTrend(symbols=["510300"], sma_lookback=5)
        s._day_data["510300"] = [
            {"close": 100}, {"close": 102}, {"close": 104},
            {"close": 106}, {"close": 108},
        ]
        sma = s._calculate_sma("510300")
        assert sma == pytest.approx(104.0)

    def test_weights_above_sma(self):
        s = VolatilityScaledTrend(symbols=["510300"], sma_lookback=3)
        s._day_data["510300"] = [
            {"close": 100}, {"close": 102}, {"close": 104},
        ]
        weights = s._calculate_weights()
        assert "510300" in weights

    def test_weights_below_sma_excluded(self):
        s = VolatilityScaledTrend(symbols=["510300"], sma_lookback=3)
        s._day_data["510300"] = [
            {"close": 110}, {"close": 108}, {"close": 100},
        ]
        weights = s._calculate_weights()
        assert "510300" not in weights


class TestSimpleMomentumStrategy:
    def test_momentum_scores(self):
        s = SimpleMomentum(symbols=["AAPL", "MSFT"], momentum_lookback=3)
        s._day_data["AAPL"] = [
            {"close": 100}, {"close": 105}, {"close": 110},
            {"close": 115}, {"close": 120},
        ]
        s._day_data["MSFT"] = [
            {"close": 400}, {"close": 398}, {"close": 395},
            {"close": 390}, {"close": 385},
        ]
        s._calculate_momentum_scores()
        assert s._momentum_scores["AAPL"] > 0
        assert s._momentum_scores["MSFT"] < 0

    def test_on_data_accumulates(self):
        s = SimpleMomentum(symbols=["AAPL"])
        s.on_data(None, {"symbol": "AAPL", "close": 150})
        s.on_data(None, {"symbol": "AAPL", "close": 152})
        assert len(s._day_data["AAPL"]) == 2

    def test_get_last_price(self):
        s = SimpleMomentum(symbols=["AAPL"])
        s._day_data["AAPL"] = [{"close": 150}, {"close": 155}]
        assert s._get_last_price("AAPL") == 155.0

    def test_get_last_price_no_data(self):
        s = SimpleMomentum(symbols=["AAPL"])
        assert s._get_last_price("AAPL") == 0.0


class TestVolatilityRegimeStrategy:
    def test_init(self):
        s = VolatilityRegime(symbols=["AAPL", "MSFT"])
        assert len(s.symbols) == 2

    def test_backtest_runs(self):
        np.random.seed(42)
        data = make_us_bars(
            ["AAPL", "MSFT", "GOOGL"], START, 80,
            {"AAPL": 150, "MSFT": 400, "GOOGL": 140},
        )
        bt = make_backtester()
        s = VolatilityRegime(symbols=["AAPL", "MSFT", "GOOGL"])
        result = run_simple_backtest(bt, data, [s], ["AAPL", "MSFT", "GOOGL"], initial_cash=1000000)
        assert result.final_nav > 0
