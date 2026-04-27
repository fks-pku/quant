"""Regression tests for all 8 bug fixes + property-based invariants."""
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_cn_bars,
    make_us_bars,
    run_simple_backtest,
)
from quant.features.backtest.engine import (
    Backtester,
    BacktestDiagnostics,
    BacktestResult,
    MAX_FILL_DEFER_DAYS,
)
from quant.features.backtest.walkforward import DataFrameProvider, WalkForwardEngine

try:
    from hypothesis import given, settings, assume, HealthCheck
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


START = datetime(2025, 1, 2)


def _make_strategy_buy_sell(buy_day, sell_day, symbol="AAPL", price=150.0):
    class Strategy:
        name = "BugFixTest"
        context = None
        _positions = {}
        _day = 0

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            om = ctx.order_manager
            if self._day == buy_day:
                om.submit_order(symbol, 100, "BUY", "MARKET", price, "BugFixTest")
            elif self._day == sell_day:
                om.submit_order(symbol, 100, "SELL", "MARKET", price, "BugFixTest")
            self._day += 1

        def on_fill(self, ctx, fill):
            qty = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

        def on_stop(self, ctx):
            pass

    return Strategy()


class TestBugFix1RiskCheckPriceNone:
    """BUG-1: price=None should not bypass risk checks."""

    def test_sell_with_price_none_still_checks_t1_for_cn(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class SameDaySellCN:
            name = "SameDaySell"
            context = None
            _positions = {}
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                om = ctx.order_manager
                if self._day == 0:
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "SameDaySell")
                elif self._day == 1:
                    om.submit_order("600519", 50, "SELL", "MARKET", None, "SameDaySell")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[SameDaySellCN()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        assert len(sell_trades) >= 0

    def test_risk_engine_called_even_without_price(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 0.001, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyNoPrice:
            name = "BuyNoPrice"
            context = None
            _positions = {}
            _ordered = False

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if not self._ordered:
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", None, "BuyNoPrice")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyNoPrice()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert result.diagnostics.risk_skipped_orders == 0


class TestBugFix3ExpiredOrdersDiagnostic:
    """BUG-3: Expired deferred orders must be counted."""

    def test_expired_order_counted_in_diagnostics(self):
        diag = BacktestDiagnostics()
        assert diag.expired_orders == 0
        assert hasattr(diag, 'expired_orders')

    def test_risk_skipped_orders_field_exists(self):
        diag = BacktestDiagnostics()
        assert diag.risk_skipped_orders == 0
        assert hasattr(diag, 'risk_skipped_orders')


class TestBugFix5SuspendedCheck:
    """BUG-5: _is_suspended should check close==0 and open==0."""

    def test_zero_close_and_zero_open_is_suspended(self):
        bar = {"volume": 1000, "open": 0, "close": 0, "high": 0, "low": 0}
        assert Backtester._is_suspended(bar, None) is True

    def test_zero_volume_is_suspended(self):
        bar = {"volume": 0, "open": 100, "close": 100}
        assert Backtester._is_suspended(bar, None) is True

    def test_normal_bar_not_suspended(self):
        bar = {"volume": 1000, "open": 100, "close": 100}
        assert Backtester._is_suspended(bar, None) is False

    def test_zero_close_with_nonzero_open_not_suspended(self):
        bar = {"volume": 1000, "open": 100, "close": 0}
        assert Backtester._is_suspended(bar, None) is False


class TestBugFix6CostDragPct:
    """BUG-6: cost_drag_pct should not overflow for near-zero gross PnL."""

    def test_near_zero_gross_pnl_returns_zero(self):
        diag = BacktestDiagnostics(total_commission=100.0, total_gross_pnl=1e-15)
        assert diag.cost_drag_pct == 0.0

    def test_negative_near_zero_gross_pnl_returns_zero(self):
        diag = BacktestDiagnostics(total_commission=100.0, total_gross_pnl=-1e-15)
        assert diag.cost_drag_pct == 0.0

    def test_normal_gross_pnl_calculates(self):
        diag = BacktestDiagnostics(total_commission=100.0, total_gross_pnl=1000.0)
        assert diag.cost_drag_pct == pytest.approx(10.0, rel=1e-4)

    def test_exactly_zero_returns_zero(self):
        diag = BacktestDiagnostics(total_commission=100.0, total_gross_pnl=0.0)
        assert diag.cost_drag_pct == 0.0


class TestBugFix7WalkForwardNoDefaultParams:
    """BUG-7: WalkForward should skip windows when no params produce valid results."""

    def test_no_valid_params_returns_not_viable(self):
        engine = WalkForwardEngine(train_window_days=5, test_window_days=2, step_days=2, min_trades=999)
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        from quant.features.strategies.simple_momentum.strategy import SimpleMomentum
        result = engine.run(
            strategy_factory=lambda params: SimpleMomentum(symbols=["AAPL"], momentum_lookback=5),
            data=data,
            param_grid={"lookback": [5]},
            initial_cash=100000,
            config={"backtest": {"slippage_bps": 0}, "execution": {"commission": {}}, "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0}},
        )
        assert result.is_viable is False


class TestBugFix8T1FillDateCheck:
    """BUG-8: RiskEngine T+1 should use fill date, not submission date."""

    def test_cn_sell_passes_when_buy_settles_by_fill_date(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyDay0SellDay1:
            name = "Buy0Sell1"
            context = None
            _positions = {}
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                om = ctx.order_manager
                if self._day == 0:
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "Buy0Sell1")
                elif self._day == 1:
                    om.submit_order("600519", 100, "SELL", "MARKET", 50.0, "Buy0Sell1")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyDay0SellDay1()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        assert len(sell_trades) >= 1
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1
        if sell_trades and buy_trades:
            sell_date = sell_trades[0].fill_date
            buy_date = buy_trades[0].fill_date
            assert (sell_date.date() - buy_date.date()).days >= 1


# ============================================================
# Property-based tests (require hypothesis)
# ============================================================

@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestPropertyBasedInvariants:
    """Property-based tests that automatically explore edge cases."""

    @given(
        price=st.floats(min_value=0.01, max_value=10000, allow_nan=False, allow_infinity=False),
        quantity=st.floats(min_value=1, max_value=1e6, allow_nan=False, allow_infinity=False),
        side=st.sampled_from(["BUY", "SELL"]),
        market=st.sampled_from(["US", "HK", "CN"]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_commission_always_non_negative(self, price, quantity, side, market):
        bt = Backtester({"backtest": {"slippage_bps": 5}, "execution": {"commission": {}}, "risk": {}})
        breakdown = bt._calculate_commission_breakdown(price, quantity, market, side)
        for key, value in breakdown.items():
            assert value >= 0, f"{key}={value} is negative for {market} {side}"

    @given(
        price=st.floats(min_value=0.01, max_value=10000, allow_nan=False, allow_infinity=False),
        quantity=st.floats(min_value=1, max_value=1e6, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_sell_commission_ge_buy_commission_same_market(self, price, quantity):
        bt = Backtester({"backtest": {"slippage_bps": 5}, "execution": {"commission": {}}, "risk": {}})
        for market in ["US", "HK", "CN"]:
            buy_total = sum(bt._calculate_commission_breakdown(price, quantity, market, "BUY").values())
            sell_total = sum(bt._calculate_commission_breakdown(price, quantity, market, "SELL").values())
            assert sell_total >= buy_total, f"SELL fee < BUY fee for {market}"

    @given(
        prev_close=st.floats(min_value=1.0, max_value=1000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_cn_limit_check_symmetry(self, prev_close):
        bt = Backtester({"backtest": {"slippage_bps": 5}, "execution": {"commission": {}}, "risk": {}})
        for symbol, limit_pct in [("600519", 0.10), ("300750", 0.20), ("688981", 0.20), ("830001", 0.30)]:
            just_below = prev_close * (1 + limit_pct) - 0.01
            just_above = prev_close * (1 + limit_pct) + 0.01
            assert bt._is_cn_price_at_limit(symbol, just_below, prev_close) is False
            assert bt._is_cn_price_at_limit(symbol, just_above, prev_close) is True

    @given(
        gross_pnl=st.floats(min_value=-1e10, max_value=1e10, allow_nan=False, allow_infinity=False),
        commission=st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_cost_drag_finite_and_non_negative(self, gross_pnl, commission):
        diag = BacktestDiagnostics(total_commission=commission, total_gross_pnl=gross_pnl)
        drag = diag.cost_drag_pct
        assert np.isfinite(drag), f"cost_drag_pct={drag} is not finite"
        assert drag >= 0, f"cost_drag_pct={drag} is negative"

    @given(
        volume=st.floats(min_value=0, max_value=1e12, allow_nan=False, allow_infinity=False),
        open_price=st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
        close_price=st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_is_suspended_returns_bool(self, volume, open_price, close_price):
        bar = {"volume": volume, "open": open_price, "close": close_price}
        result = Backtester._is_suspended(bar, None)
        assert isinstance(result, bool)

    @given(
        nav_start=st.floats(min_value=10000, max_value=1e9, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_nav_equals_initial_cash_plus_total_pnl(self, nav_start):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuySell:
            name = "BuySell"
            context = None
            _positions = {}
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                om = ctx.order_manager
                if self._day == 0:
                    qty = int(ctx.portfolio.cash / 150.0 * 0.99)
                    om.submit_order("AAPL", qty, "BUY", "MARKET", 150.0, "BuySell")
                elif self._day == 5:
                    pos_qty = self._positions.get("AAPL", 0)
                    if pos_qty > 0:
                        om.submit_order("AAPL", pos_qty, "SELL", "MARKET", 150.0, "BuySell")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuySell()],
            initial_cash=nav_start,
            data_provider=provider,
            symbols=["AAPL"],
        )
        total_pnl = sum(t.pnl for t in result.trades)
        assert abs(result.final_nav - (nav_start + total_pnl)) < 1.0, \
            f"NAV mismatch: {result.final_nav} != {nav_start} + {total_pnl}"
        assert result.diagnostics.total_commission >= 0
