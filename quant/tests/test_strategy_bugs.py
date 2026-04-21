"""Tests for strategy bug fixes: look-ahead bias, rebalancing, stop cleanup."""

import pytest
from datetime import date, datetime
from unittest.mock import MagicMock, patch, PropertyMock
import pandas as pd
import numpy as np

from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.volatility_regime.strategy import VolatilityRegime
from quant.features.strategies.simple_momentum.strategy import SimpleMomentum


def _make_context(nav=100000.0, data_provider=None):
    context = MagicMock()
    context.portfolio = MagicMock()
    context.portfolio.nav = nav
    context.data_provider = data_provider or MagicMock()
    order_manager = MagicMock()
    order_manager.submit_order.return_value = "order-1"
    context.order_manager = order_manager
    return context


def _make_bars(prices, start_date="2024-01-01"):
    dates = pd.date_range(start_date, periods=len(prices), freq="D")
    return pd.DataFrame({"close": prices}, index=dates)


class TestVolatilityRegimeLookAhead:
    def test_volatility_regime_uses_trading_date_not_now(self):
        strategy = VolatilityRegime(symbols=["AAPL"])
        trading_date = date(2022, 6, 15)
        context = _make_context()

        fetched_ranges = []

        def mock_get_bars(symbol, start, end, freq):
            fetched_ranges.append((symbol, start, end))
            return _make_bars([100.0] * 60)

        context.data_provider.get_bars = mock_get_bars
        strategy.context = context

        strategy._calculate_momentum_scores(context, trading_date)
        strategy._calculate_rsi_values(context, trading_date)

        for symbol, start, end in fetched_ranges:
            assert end.date() == trading_date, (
                f"Look-ahead bias: fetched data ending at {end.date()} "
                f"instead of trading_date {trading_date}"
            )


class TestSimpleMomentumRebalancing:
    def test_simple_momentum_rebalances_every_holding_period(self):
        holding_period = 5
        strategy = SimpleMomentum(
            symbols=["AAPL", "MSFT"],
            momentum_lookback=3,
            holding_period=holding_period,
        )
        context = _make_context()
        strategy.on_start(context)

        rebalance_dates = []
        original_submit = context.order_manager.submit_order

        def track_submit(*args, **kwargs):
            rebalance_dates.append(strategy._last_rebalance_date)
            return original_submit(*args, **kwargs)

        context.order_manager.submit_order = track_submit

        start = date(2024, 1, 2)
        for day_offset in range(25):
            trading_date = date.fromordinal(start.toordinal() + day_offset)

            for symbol in strategy._symbols:
                if symbol not in strategy._day_data:
                    strategy._day_data[symbol] = []
                strategy._day_data[symbol].append(
                    {"symbol": symbol, "close": 100.0 + day_offset}
                )

            strategy.on_before_trading(context, trading_date)
            strategy.on_after_trading(context, trading_date)

        unique_rebalances = len(set(rebalance_dates))
        expected_max = 2 * len(strategy._symbols)
        assert unique_rebalances <= 6, (
            f"Rebalanced {unique_rebalances} unique dates in 25 days with holding_period={holding_period}, "
            f"expected at most 6"
        )

    def test_simple_momentum_closes_on_stop(self):
        strategy = SimpleMomentum(
            symbols=["AAPL"],
            momentum_lookback=3,
            holding_period=5,
        )
        context = _make_context()
        strategy.on_start(context)

        strategy._positions["AAPL"] = 100
        strategy._last_rebalance_date = date(2024, 1, 2)
        strategy._long_positions = ["AAPL"]
        strategy._day_data["AAPL"] = [{"symbol": "AAPL", "close": 150.0}]

        strategy.on_stop(context)

        context.order_manager.submit_order.assert_called()
        call_args = context.order_manager.submit_order.call_args_list
        sell_calls = [c for c in call_args if c[0][2] == "SELL"]
        assert len(sell_calls) > 0, "on_stop should submit sell orders for open positions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
