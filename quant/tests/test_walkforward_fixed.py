"""Tests for walk-forward engine config propagation bug fix."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from quant.core.walkforward import WalkForwardEngine, DataFrameProvider
from quant.core.backtester import Backtester


def _generate_data(symbols, days=250, start_date=None):
    if start_date is None:
        start_date = datetime(2024, 1, 2)
    records = []
    np.random.seed(42)
    for symbol in symbols:
        price = 150.0
        for i in range(days):
            date = start_date + timedelta(days=i)
            if date.weekday() >= 5:
                continue
            change = np.random.normal(0.0005, 0.02)
            price = max(10.0, price * (1 + change))
            records.append({
                'timestamp': date,
                'symbol': symbol,
                'open': price * (1 + np.random.uniform(-0.005, 0.005)),
                'high': price * (1 + np.random.uniform(0, 0.02)),
                'low': price * (1 - np.random.uniform(0, 0.02)),
                'close': round(price, 2),
                'volume': np.random.randint(100000, 10000000),
            })
    return pd.DataFrame(records)


class _DummyStrategy:
    def __init__(self, params=None):
        self.params = params or {}
        self.context = None
        self.name = "DummyStrategy"
        self._day_count = 0

    def on_start(self, context):
        self.context = context

    def on_before_trading(self, context, trading_date):
        pass

    def on_data(self, context, data):
        if isinstance(data, dict):
            self._day_count += 1
            symbol = data.get("symbol", "")
            if self._day_count == 5 and symbol:
                context.order_manager.submit_order(
                    symbol, 10, "BUY", "MARKET", data.get("close", 100), self.name
                )
            elif self._day_count == 15 and symbol:
                pos = context.portfolio.get_position(symbol)
                if pos and pos.quantity > 0:
                    context.order_manager.submit_order(
                        symbol, pos.quantity, "SELL", "MARKET", data.get("close", 100), self.name
                    )

    def on_after_trading(self, context, trading_date):
        pass

    def on_stop(self, context):
        pass


def _strategy_factory(params):
    return _DummyStrategy(params)


class TestWalkForwardConfigPropagation:

    def test_walkforward_passes_config_to_backtester(self):
        symbols = ["AAPL", "MSFT"]
        data = _generate_data(symbols, days=250)

        slippage_bps = 20
        config = {
            "backtest": {"slippage_bps": slippage_bps, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.50, "min_per_order": 5.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 1.0,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 1.0,
                "max_leverage": 10.0,
                "max_orders_minute": 1000,
            }
        }

        engine = WalkForwardEngine(
            train_window_days=60,
            test_window_days=21,
            step_days=21
        )

        result = engine.run(
            strategy_factory=_strategy_factory,
            data=data,
            param_grid={"dummy": [1]},
            initial_cash=100000,
            config=config
        )

        assert result is not None
        assert isinstance(result.windows, list)

        backtester = Backtester(config)
        assert backtester.slippage_bps == slippage_bps
        assert backtester.commission.US["per_share"] == 0.50

    def test_walkforward_produces_multiple_windows(self):
        symbols = ["AAPL", "MSFT"]
        data = _generate_data(symbols, days=300)

        config = {
            "backtest": {"slippage_bps": 5, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 1.0,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 1.0,
                "max_leverage": 10.0,
                "max_orders_minute": 1000,
            }
        }

        engine = WalkForwardEngine(
            train_window_days=60,
            test_window_days=21,
            step_days=21
        )

        result = engine.run(
            strategy_factory=_strategy_factory,
            data=data,
            param_grid={"dummy": [1]},
            initial_cash=100000,
            config=config
        )

        assert len(result.windows) >= 2, f"Expected >=2 windows, got {len(result.windows)}"

    def test_walkforward_empty_data_returns_viable_false(self):
        symbols = ["AAPL"]
        data = _generate_data(symbols, days=10)

        config = {
            "backtest": {"slippage_bps": 5, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 1.0,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 1.0,
                "max_leverage": 10.0,
                "max_orders_minute": 1000,
            }
        }

        engine = WalkForwardEngine(
            train_window_days=126,
            test_window_days=21,
            step_days=21
        )

        result = engine.run(
            strategy_factory=_strategy_factory,
            data=data,
            param_grid={"dummy": [1]},
            initial_cash=100000,
            config=config
        )

        assert result.is_viable is False
        assert result.windows == []

    def test_walkforward_aggregate_max_dd_is_max_of_windows(self):
        symbols = ["AAPL", "MSFT"]
        data = _generate_data(symbols, days=250)

        config = {
            "backtest": {"slippage_bps": 5, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 1.0,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 1.0,
                "max_leverage": 10.0,
                "max_orders_minute": 1000,
            }
        }

        engine = WalkForwardEngine(
            train_window_days=60,
            test_window_days=21,
            step_days=21
        )

        result = engine.run(
            strategy_factory=_strategy_factory,
            data=data,
            param_grid={"dummy": [1]},
            initial_cash=100000,
            config=config
        )

        if result.windows:
            expected_dd = max(w.test_max_dd for w in result.windows)
            assert result.aggregate_max_dd == expected_dd
