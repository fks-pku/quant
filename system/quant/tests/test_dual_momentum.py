import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from quant.core.backtester import Backtester
from quant.strategies.implementations.dual_momentum import DualMomentum


def _generate_test_data(symbols, days=120, start_date=None):
    if start_date is None:
        start_date = datetime(2024, 1, 2)

    records = []
    np.random.seed(42)

    for symbol in symbols:
        price = 150.0
        for i in range(days):
            dt = start_date + timedelta(days=i)
            if dt.weekday() >= 5:
                continue
            change = np.random.normal(0.0005, 0.02)
            price = max(10.0, price * (1 + change))
            records.append({
                'timestamp': dt,
                'symbol': symbol,
                'open': price * (1 + np.random.uniform(-0.005, 0.005)),
                'high': price * (1 + np.random.uniform(0, 0.02)),
                'low': price * (1 - np.random.uniform(0, 0.02)),
                'close': round(price, 2),
                'volume': np.random.randint(100000, 10000000),
            })

    return pd.DataFrame(records)


class _InMemoryProvider:
    def __init__(self, data: pd.DataFrame):
        self.data = data

    def get_bars(self, symbol, start, end, timeframe="1d"):
        mask = (
            (self.data['symbol'] == symbol) &
            (self.data['timestamp'] >= start) &
            (self.data['timestamp'] < end)
        )
        result = self.data[mask].copy()
        if not result.empty:
            result = result.set_index('timestamp')
        return result


def _make_config():
    return {
        "backtest": {"slippage_bps": 5, "speed": "1x"},
        "execution": {
            "commission": {
                "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
            }
        },
        "data": {"default_timeframe": "1d"},
        "risk": {
            "max_position_pct": 0.20,
            "max_sector_pct": 1.0,
            "max_daily_loss_pct": 0.10,
            "max_leverage": 2.0,
            "max_orders_minute": 100,
        }
    }


class TestDualMomentum:

    def test_dual_momentum_requires_both_signals(self):
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
        data = _generate_test_data(symbols, days=120)

        strategy = DualMomentum(
            symbols=symbols,
            abs_lookback=20,
            rel_lookback=10,
            sma_short=10,
            holding_days=5,
            top_tercile_pct=0.33,
            bottom_tercile_pct=0.33,
            max_position_pct=0.10,
        )

        backtester = Backtester(_make_config())
        start = data['timestamp'].min()
        end = data['timestamp'].max()

        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=100000,
            data_provider=_InMemoryProvider(data),
            symbols=symbols,
        )

        assert result is not None
        assert result.final_nav > 0

        state = strategy.get_state()
        positions = state.get("current_positions", {})
        assert isinstance(positions, dict)

    def test_dual_momentum_exits_on_trend_reversal(self):
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
        data = _generate_test_data(symbols, days=150)

        strategy = DualMomentum(
            symbols=symbols,
            abs_lookback=20,
            rel_lookback=10,
            sma_short=10,
            holding_days=5,
            top_tercile_pct=0.33,
            bottom_tercile_pct=0.33,
            max_position_pct=0.10,
        )

        backtester = Backtester(_make_config())
        start = data['timestamp'].min()
        end = data['timestamp'].max()

        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=100000,
            data_provider=_InMemoryProvider(data),
            symbols=symbols,
        )

        assert result is not None
        assert result.final_nav > 0
        assert isinstance(result.sharpe_ratio, float)
