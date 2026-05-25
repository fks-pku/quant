from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_large_cap_low_vol_momentum_timing.strategy import (
    AShareLargeCapLowVolMomentumTimingStrategy,
    STRATEGY_NAME,
)
from quant.features.strategies.registry import StrategyRegistry


def test_large_cap_timing_strategy_is_registered():
    assert StrategyRegistry.is_registered(STRATEGY_NAME)


def test_large_cap_timing_strategy_is_not_single_stock():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(symbols=["000858", "600519", "000300"])

    assert strategy.timing_symbol == "000300"
    assert strategy.trade_symbols == ["000858", "600519"]
    assert strategy.max_positions == 30


def test_large_cap_timing_strategy_excludes_timing_symbol_from_candidates():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(symbols=["000858", "000300"])

    reason = strategy._candidate_rejection("000300", {"close": 4000.0})

    assert reason == "timing_symbol"


def test_large_cap_timing_strategy_stock_dividend_fill_adjusts_internal_cost_and_peak():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(symbols=["000858", "000300"])
    strategy.on_fill(
        None,
        SimpleNamespace(symbol="000858", quantity=100, side="BUY", fill_price=10.0, price=10.0),
    )
    strategy._peak_prices["000858"] = 12.0

    strategy.on_fill(
        None,
        SimpleNamespace(symbol="000858", quantity=10, side="BUY", fill_price=0.0, price=0.0),
    )

    assert strategy._positions["000858"] == 110
    assert strategy._entry_prices["000858"] == pytest.approx(1000.0 / 110.0)
    assert strategy._peak_prices["000858"] == pytest.approx(12.0 * 100.0 / 110.0)


def test_large_cap_timing_strategy_trend_elastic_profile_scores_trend_strength():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        score_profile="trend_elastic",
        symbol_trend_ma=60,
    )

    assert strategy.score_specs[0] == ("trend_strength", 0.35, True)


def test_large_cap_timing_strategy_rule_profile_uses_quality_trend_scores():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        score_profile="rule_based_quality_trend",
    )

    assert ("volatility", 0.15, False) in strategy.score_specs
    assert ("pb", 0.10, False) in strategy.score_specs


def test_large_cap_timing_strategy_rule_filters_high_valuation():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        max_pb=5.0,
        max_ps_ttm=10.0,
    )

    snapshot = strategy._strategy_snapshot(
        "000858",
        {"symbol": "000858", "close": 100.0, "pb": 8.0, "ps_ttm": 2.0},
        {"symbol": "000858", "total_mv": 1000.0, "circ_mv": 800.0},
    )

    assert snapshot["rejection_reason"] == "high_pb"


def test_large_cap_timing_strategy_rule_filters_weak_recent_momentum():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        min_recent_momentum=0.02,
    )
    strategy._return = lambda symbol, lookback: 0.01

    snapshot = strategy._strategy_snapshot(
        "000858",
        {"symbol": "000858", "close": 100.0, "pb": 2.0, "ps_ttm": 2.0},
        {"symbol": "000858", "total_mv": 1000.0, "circ_mv": 800.0},
    )

    assert snapshot["rejection_reason"] == "weak_recent_momentum"


def test_large_cap_timing_strategy_symbol_trend_exit_uses_adjusted_ma():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        symbol_trend_ma=60,
        symbol_exit_buffer=0.98,
    )
    strategy._positions["000858"] = 100
    strategy._day_data["000858"] = [{"symbol": "000858", "close": 100.0, "adj_close": 100.0} for _ in range(60)]
    strategy._day_data["000858"].append({"symbol": "000858", "close": 95.0, "adj_close": 95.0})

    reason = strategy._position_exit_reason("000858", strategy._day_data["000858"][-1])

    assert reason == "symbol_trend_exit"


def test_large_cap_timing_strategy_can_disable_market_timing_gate():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        use_market_timing=False,
    )

    assert strategy._update_timing_state() is True
    assert strategy.get_guard_diagnostics()["timing"]["market_timing"] == "disabled"


def test_large_cap_timing_strategy_can_weight_by_target_slots():
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(
        symbols=["000858", "000300"],
        target_weight_slots=8,
    )

    assert strategy.get_state()["parameters"]["target_weight_slots"] == 8
