"""分析指标测试 — Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor。"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

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
    calculate_ulcer_index,
    calculate_gain_to_pain_ratio,
    calculate_tail_ratio,
    calculate_recovery_factor,
    calculate_statistical_significance,
    calculate_performance_metrics,
    PerformanceMetrics,
)
from quant.domain.models.trade import Trade


def make_trade(symbol, qty, entry, exit_, side, pnl=0, realized_pnl=0):
    return Trade(
        symbol=symbol,
        quantity=qty,
        entry_price=entry,
        exit_price=exit_,
        entry_time=datetime(2025, 1, 2),
        exit_time=datetime(2025, 1, 5),
        side=side,
        pnl=pnl,
        realized_pnl=realized_pnl,
    )


def make_equity_curve(values, start=datetime(2025, 1, 2)):
    dates = [start + timedelta(days=i) for i in range(len(values))]
    return pd.Series(values, index=dates)


class TestSharpe:
    def test_empty_returns_zero(self):
        assert calculate_sharpe(pd.Series(dtype=float)) == 0.0

    def test_zero_std_returns_zero(self):
        assert calculate_sharpe(pd.Series([0.01, 0.01, 0.01])) == 0.0

    def test_positive_sharpe(self):
        returns = pd.Series([0.01, 0.02, 0.015, 0.005, 0.01])
        assert calculate_sharpe(returns) > 0

    def test_negative_sharpe(self):
        returns = pd.Series([-0.01, -0.02, -0.015, -0.005, -0.01])
        assert calculate_sharpe(returns) < 0


class TestSortino:
    def test_empty_returns_zero(self):
        assert calculate_sortino(pd.Series(dtype=float)) == 0.0

    def test_all_positive_returns_inf(self):
        returns = pd.Series([0.01, 0.02, 0.015])
        assert calculate_sortino(returns) == float('inf')

    def test_mixed_returns(self):
        returns = pd.Series([0.02, -0.01, 0.03, -0.005, 0.01])
        assert calculate_sortino(returns) > 0


class TestMaxDrawdown:
    def test_empty_returns_zero(self):
        dd, dd_pct, _, _ = calculate_max_drawdown(pd.Series(dtype=float))
        assert dd == 0.0

    def test_monotonic_increase_no_drawdown(self):
        curve = make_equity_curve([100, 110, 120, 130, 140])
        dd, dd_pct, _, _ = calculate_max_drawdown(curve)
        assert dd == 0.0

    def test_drawdown_detected(self):
        curve = make_equity_curve([100, 110, 90, 95, 100])
        dd, dd_pct, peak, trough = calculate_max_drawdown(curve)
        assert dd < 0
        assert dd_pct < 0

    def test_large_drawdown(self):
        curve = make_equity_curve([100, 50, 80, 60, 40])
        dd, dd_pct, _, _ = calculate_max_drawdown(curve)
        assert dd == pytest.approx(-60.0, rel=1e-4)
        assert dd_pct == pytest.approx(-0.6, rel=1e-4)


class TestCalmar:
    def test_zero_dd_returns_zero(self):
        returns = pd.Series([0.01, 0.02])
        assert calculate_calmar(returns, 0.0) == 0.0

    def test_positive_calmar(self):
        returns = pd.Series([0.01, 0.02, -0.005, 0.015])
        assert calculate_calmar(returns, -0.01) > 0


class TestWinRate:
    def test_no_trades(self):
        assert calculate_win_rate([]) == 0.0

    def test_all_winners(self):
        trades = [make_trade("A", 100, 10, 12, "SELL", pnl=100) for _ in range(3)]
        assert calculate_win_rate(trades) == 1.0

    def test_all_losers(self):
        trades = [make_trade("A", 100, 12, 10, "SELL", pnl=-100) for _ in range(3)]
        assert calculate_win_rate(trades) == 0.0

    def test_mixed(self):
        trades = [
            make_trade("A", 100, 10, 12, "SELL", pnl=100),
            make_trade("A", 100, 12, 10, "SELL", pnl=-50),
        ]
        assert calculate_win_rate(trades) == 0.5

    def test_buy_only_not_counted(self):
        trades = [make_trade("A", 100, 10, 10, "BUY", pnl=0)]
        assert calculate_win_rate(trades) == 0.0


class TestProfitFactor:
    def test_no_trades(self):
        assert calculate_profit_factor([]) == 0.0

    def test_all_winners_inf(self):
        trades = [make_trade("A", 100, 10, 12, "SELL", pnl=100)]
        assert calculate_profit_factor(trades) == float('inf')

    def test_profit_factor_calc(self):
        trades = [
            make_trade("A", 100, 10, 12, "SELL", pnl=200),
            make_trade("A", 100, 12, 10, "SELL", pnl=-100),
        ]
        assert calculate_profit_factor(trades) == pytest.approx(2.0, rel=1e-4)


class TestPayoffRatio:
    def test_no_trades(self):
        assert calculate_payoff_ratio([]) == 0.0

    def test_only_winners(self):
        trades = [make_trade("A", 100, 10, 12, "SELL", pnl=100)]
        assert calculate_payoff_ratio(trades) == 0.0

    def test_calc(self):
        trades = [
            make_trade("A", 100, 10, 12, "SELL", pnl=200),
            make_trade("A", 100, 12, 10, "SELL", pnl=-100),
        ]
        assert calculate_payoff_ratio(trades) == pytest.approx(2.0, rel=1e-4)


class TestExpectancy:
    def test_no_trades(self):
        assert calculate_expectancy([]) == 0.0

    def test_positive_expectancy(self):
        trades = [
            make_trade("A", 100, 10, 12, "SELL", pnl=200),
            make_trade("A", 100, 12, 10, "SELL", pnl=-50),
        ]
        assert calculate_expectancy(trades) > 0


class TestRollingSharpe:
    def test_too_short_returns_empty(self):
        returns = pd.Series([0.01, 0.02])
        assert len(calculate_rolling_sharpe(returns, window=63)) == 0

    def test_enough_data(self):
        returns = pd.Series(np.random.normal(0.001, 0.02, 100))
        rs = calculate_rolling_sharpe(returns, window=20)
        assert len(rs) > 0


class TestUlcerIndex:
    def test_empty(self):
        assert calculate_ulcer_index(pd.Series(dtype=float)) == 0.0

    def test_monotonic_increase_low(self):
        curve = make_equity_curve([100, 110, 120, 130, 140, 150, 160, 170])
        result = calculate_ulcer_index(curve)
        assert result == 0.0 or np.isnan(result) or result < 1.0


class TestGainToPainRatio:
    def test_no_trades(self):
        assert calculate_gain_to_pain_ratio([]) == 0.0

    def test_only_losses(self):
        trades = [make_trade("A", 100, 12, 10, "SELL", pnl=-100)]
        assert calculate_gain_to_pain_ratio(trades) == 0.0


class TestTailRatio:
    def test_too_short_returns_one(self):
        returns = pd.Series([0.01, 0.02, 0.03])
        assert calculate_tail_ratio(returns) == 1.0

    def test_symmetric_returns_near_one(self):
        returns = pd.Series(np.random.normal(0, 0.01, 100))
        tr = calculate_tail_ratio(returns)
        assert 0.5 < tr < 2.0


class TestRecoveryFactor:
    def test_no_trades(self):
        assert calculate_recovery_factor([], -0.1) == 0.0

    def test_zero_dd(self):
        trades = [make_trade("A", 100, 10, 12, "SELL", pnl=100)]
        assert calculate_recovery_factor(trades, 0.0) == float('inf')


class TestStatisticalSignificance:
    def test_empty(self):
        result = calculate_statistical_significance(pd.Series(dtype=float))
        assert result["is_significant"] is False

    def test_significant_positive_mean(self):
        returns = pd.Series(np.random.normal(0.01, 0.005, 100))
        result = calculate_statistical_significance(returns)
        assert result["t_stat"] > 0
        assert result["p_value"] < 1.0

    def test_zero_std(self):
        returns = pd.Series([0.01, 0.01, 0.01])
        result = calculate_statistical_significance(returns)
        assert result["is_significant"] is False


class TestPerformanceMetrics:
    def test_empty_equity_curve(self):
        metrics = calculate_performance_metrics(pd.Series(dtype=float), [])
        assert metrics.total_return == 0.0
        assert metrics.sharpe_ratio == 0.0
        assert metrics.total_trades == 0

    def test_normal_metrics(self):
        np.random.seed(42)
        values = [100000]
        for _ in range(50):
            values.append(values[-1] * (1 + np.random.normal(0.001, 0.02)))
        curve = make_equity_curve(values)
        trades = [make_trade("A", 100, 100, 105, "SELL", pnl=200, realized_pnl=200)]
        metrics = calculate_performance_metrics(curve, trades)
        assert metrics.total_return != 0.0
        assert metrics.total_trades == 1
        assert isinstance(metrics, PerformanceMetrics)
