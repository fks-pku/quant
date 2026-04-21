"""Unit tests for analytics module."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from quant.features.backtest.analytics import (
    calculate_sharpe,
    calculate_sortino,
    calculate_max_drawdown,
    calculate_calmar,
    calculate_win_rate,
    calculate_profit_factor,
    calculate_payoff_ratio,
    calculate_expectancy,
    calculate_rolling_sharpe,
    calculate_statistical_significance,
    calculate_performance_metrics,
)
from quant.shared.models.trade import Trade


def _make_equity_curve(initial=100000, days=100, daily_return=0.001):
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    values = [initial]
    for i in range(1, days):
        values.append(values[-1] * (1 + daily_return))
    return pd.Series(values, index=dates)


def _make_trade(pnl, entry_days_ago=5):
    now = datetime.now()
    return Trade(
        entry_time=now - timedelta(days=entry_days_ago),
        exit_time=now,
        symbol="AAPL",
        side="SELL",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        pnl=pnl,
    )


class TestSharpeRatio:
    def test_positive_returns(self):
        returns = pd.Series(np.random.normal(0.01, 0.02, 252))
        sharpe = calculate_sharpe(returns)
        assert sharpe > 0

    def test_empty_returns(self):
        sharpe = calculate_sharpe(pd.Series(dtype=float))
        assert sharpe == 0.0

    def test_zero_std(self):
        returns = pd.Series([0.0] * 10)
        sharpe = calculate_sharpe(returns)
        assert sharpe == 0.0


class TestSortinoRatio:
    def test_positive_returns(self):
        returns = pd.Series([0.01, -0.005, 0.02, 0.005, -0.003] * 50)
        sortino = calculate_sortino(returns)
        assert sortino > 0

    def test_empty_returns(self):
        assert calculate_sortino(pd.Series(dtype=float)) == 0.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        curve = pd.Series([100, 101, 102, 103, 104])
        dd, dd_pct, peak, trough = calculate_max_drawdown(curve)
        assert dd >= 0
        assert dd_pct >= 0

    def test_with_drawdown(self):
        curve = pd.Series([100, 110, 90, 95, 85])
        dd, dd_pct, peak, trough = calculate_max_drawdown(curve)
        assert dd < 0
        assert dd_pct < 0

    def test_empty_curve(self):
        dd, dd_pct, peak, trough = calculate_max_drawdown(pd.Series(dtype=float))
        assert dd == 0.0


class TestCalmarRatio:
    def test_positive_with_drawdown(self):
        returns = pd.Series([0.01] * 252)
        calmar = calculate_calmar(returns, max_dd=0.1)
        assert calmar > 0

    def test_zero_drawdown(self):
        returns = pd.Series([0.01] * 252)
        calmar = calculate_calmar(returns, max_dd=0.0)
        assert calmar == 0.0


class TestWinRate:
    def test_all_winners(self):
        trades = [_make_trade(10), _make_trade(20), _make_trade(5)]
        assert calculate_win_rate(trades) == 1.0

    def test_mixed(self):
        trades = [_make_trade(10), _make_trade(-5), _make_trade(10)]
        assert calculate_win_rate(trades) == pytest.approx(2 / 3, abs=0.01)

    def test_empty(self):
        assert calculate_win_rate([]) == 0.0


class TestProfitFactor:
    def test_all_winners(self):
        trades = [_make_trade(10), _make_trade(20)]
        assert calculate_profit_factor(trades) == float('inf')

    def test_mixed(self):
        trades = [_make_trade(20), _make_trade(-10)]
        assert calculate_profit_factor(trades) == 2.0

    def test_empty(self):
        assert calculate_profit_factor([]) == 0.0


class TestPayoffRatio:
    def test_mixed(self):
        trades = [_make_trade(20), _make_trade(-10)]
        assert calculate_payoff_ratio(trades) == 2.0

    def test_only_winners(self):
        trades = [_make_trade(10)]
        assert calculate_payoff_ratio(trades) == 0.0


class TestExpectancy:
    def test_positive(self):
        trades = [_make_trade(10), _make_trade(-5)]
        exp = calculate_expectancy(trades)
        assert exp == pytest.approx(2.5, abs=0.01)

    def test_empty(self):
        assert calculate_expectancy([]) == 0.0


class TestRollingSharpe:
    def test_basic(self):
        returns = pd.Series(np.random.normal(0.001, 0.02, 252))
        rolling = calculate_rolling_sharpe(returns, window=63)
        assert len(rolling) == 252
        assert rolling.isna().sum() < 63

    def test_too_short(self):
        returns = pd.Series([0.01] * 10)
        rolling = calculate_rolling_sharpe(returns, window=63)
        assert rolling.empty or len(rolling) == 10


class TestStatisticalSignificance:
    def test_significant_positive_mean(self):
        returns = pd.Series(np.random.normal(0.01, 0.01, 100))
        result = calculate_statistical_significance(returns)
        assert result["t_stat"] > 0
        assert "is_significant" in result

    def test_empty(self):
        result = calculate_statistical_significance(pd.Series(dtype=float))
        assert result["is_significant"] is False


class TestPerformanceMetrics:
    def test_full_metrics(self):
        np.random.seed(42)
        curve = _make_equity_curve(days=100, daily_return=0.001)
        noise = np.random.normal(0, 0.005, len(curve))
        curve = curve * (1 + pd.Series(noise, index=curve.index)).cumprod()
        trades = [_make_trade(10), _make_trade(-5), _make_trade(20)]
        metrics = calculate_performance_metrics(curve, trades)
        assert metrics.total_return > 0
        assert isinstance(metrics.sharpe_ratio, float)
        assert metrics.total_trades == 3
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 1
        assert hasattr(metrics, "rolling_sharpe")
        assert hasattr(metrics, "statistical_significance")

    def test_empty_curve(self):
        metrics = calculate_performance_metrics(pd.Series(dtype=float), [])
        assert metrics.total_return == 0.0
        assert metrics.total_trades == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
