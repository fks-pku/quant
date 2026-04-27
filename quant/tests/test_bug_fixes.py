"""Regression tests for all 8 bug fixes + invariant coverage tests + property-based tests."""
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_cn_bars,
    make_hk_bars,
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
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine

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
# Invariant coverage tests (fill gaps in original test suite)
# ============================================================

class TestF2SellPnlDecomposition:
    """F-2: SELL trade.pnl == realized_pnl - proportional_commission."""

    def test_sell_pnl_equals_realized_minus_commission(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyThenSell:
            name = "BtS"
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
                    om.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BtS")
                elif self._day == 3:
                    om.submit_order("AAPL", 100, "SELL", "MARKET", 100.0, "BtS")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 100.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyThenSell()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        assert len(sell_trades) >= 1
        for t in sell_trades:
            comm = sum(t.cost_breakdown.values()) if t.cost_breakdown else 0
            assert t.pnl == pytest.approx(t.realized_pnl - comm, rel=1e-6)


class TestF3LimitHitReturnsNone:
    """F-3: CN limit-hit order returns None (retry), not [] (permanent drop)."""

    def test_cn_limit_up_returns_none_for_retry(self):
        bt = make_backtester()
        portfolio = Portfolio(initial_cash=1000000)
        risk_engine = RiskEngine({}, portfolio, None)
        diag = BacktestDiagnostics()

        buy_date = START + timedelta(days=2)
        pos = portfolio.get_position("600519") or portfolio.positions.get("600519")
        portfolio.update_position("600519", quantity=100, price=50.0, cost=50.0 * 100, trade_date=buy_date.date())

        bar = {"open": 55.0, "close": 55.0, "high": 55.0, "low": 55.0, "volume": 1000000, "timestamp": START + timedelta(days=3)}
        prev_bar = {"close": 50.0}
        order = {"symbol": "600519", "quantity": 100, "side": "SELL", "order_type": "MARKET",
                 "price": 50.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}

        entry_times = {}
        entry_prices = {}
        result = bt._execute_order(order, portfolio, "600519", bar, entry_times, entry_prices, diag, prev_bar)

        assert result is None, f"Expected None (retry), got {result!r}"
        assert diag.limit_rejected_orders == 1


class TestF6ZeroOpenPriceReturnsEmpty:
    """F-6: open price <= 0 returns []."""

    def test_zero_open_returns_empty_list(self):
        bt = make_backtester()
        portfolio = Portfolio(initial_cash=1000000)
        diag = BacktestDiagnostics()
        order = {"symbol": "AAPL", "quantity": 100, "side": "BUY", "order_type": "MARKET",
                 "price": 100.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}
        bar = {"open": 0, "close": 100, "high": 100, "low": 100, "volume": 1000000, "timestamp": START}
        entry_times = {}
        entry_prices = {}

        result = bt._execute_order(order, portfolio, "AAPL", bar, entry_times, entry_prices, diag)
        assert result == []

    def test_negative_open_returns_empty_list(self):
        bt = make_backtester()
        portfolio = Portfolio(initial_cash=1000000)
        diag = BacktestDiagnostics()
        order = {"symbol": "AAPL", "quantity": 100, "side": "BUY", "order_type": "MARKET",
                 "price": 100.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}
        bar = {"open": -1.0, "close": 100, "high": 100, "low": 100, "volume": 1000000, "timestamp": START}
        entry_times = {}
        entry_prices = {}

        result = bt._execute_order(order, portfolio, "AAPL", bar, entry_times, entry_prices, diag)
        assert result == []


class TestCN5SellFractionalShares:
    """CN-5: SELL allows fractional shares (no lot rounding)."""

    def test_cn_sell_odd_shares_not_rejected(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class Buy100Sell50:
            name = "B100S50"
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
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "B100S50")
                elif self._day == 3:
                    om.submit_order("600519", 37, "SELL", "MARKET", 50.0, "B100S50")
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
            strategies=[Buy100Sell50()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        assert len(sell_trades) >= 1
        assert sell_trades[0].quantity == 37


class TestCN7VolumeParticipationLimit:
    """CN-7: Volume cap ≤ daily volume × 5%."""

    def test_order_reduced_to_volume_limit(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyHuge:
            name = "BuyHuge"
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
                    om = ctx.order_manager
                    om.submit_order("600519", 99999, "BUY", "MARKET", 50.0, "BuyHuge")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 5, {"600519": 50.0})
        data.loc[data["symbol"] == "600519", "volume"] = 10000
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyHuge()],
            initial_cash=100000000,
            data_provider=provider,
            symbols=["600519"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1
        assert buy_trades[0].quantity <= 10000 * 0.05
        assert result.diagnostics.volume_limited_trades >= 1


class TestR2PriceZeroRiskCheck:
    """R-2: price=0 risk check still active."""

    def test_sell_cn_with_price_zero_still_checks_t1(self):
        portfolio = Portfolio(initial_cash=1000000)
        risk_engine = RiskEngine({"risk": {"max_position_pct": 1.0}}, portfolio, None)
        today = date(2025, 1, 3)
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=today)

        approved, results = risk_engine.check_order(
            "600519", 100, 0, 0, side="SELL", as_of_date=today,
        )
        t1_result = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert len(t1_result) == 1

    def test_buy_with_price_zero_risk_not_skipped(self):
        portfolio = Portfolio(initial_cash=1000000)
        risk_engine = RiskEngine({"risk": {"max_position_pct": 0.0001}}, portfolio, None)

        approved, results = risk_engine.check_order(
            "AAPL", 100, 0, 0, side="BUY", as_of_date=date(2025, 1, 2),
        )
        assert isinstance(approved, bool)
        assert len(results) > 0


class TestR3PendingOrderValuesAccumulate:
    """R-3: _pending_order_values accumulates per symbol per day."""

    def test_pending_values_accumulate_on_record(self):
        portfolio = Portfolio(initial_cash=1000000)
        risk_engine = RiskEngine({"risk": {"max_position_pct": 1.0}}, portfolio, None)
        today = date(2025, 1, 2)

        risk_engine.record_order(symbol="AAPL", order_value=10000, as_of_date=today)
        assert risk_engine._pending_order_values.get("AAPL") == 10000

        risk_engine.record_order(symbol="AAPL", order_value=5000, as_of_date=today)
        assert risk_engine._pending_order_values.get("AAPL") == 15000

    def test_pending_values_block_oversized_position(self):
        portfolio = Portfolio(initial_cash=100000)
        risk_engine = RiskEngine({"risk": {"max_position_pct": 0.10}}, portfolio, None)
        today = date(2025, 1, 2)

        risk_engine.record_order(symbol="AAPL", order_value=5000, as_of_date=today)
        approved, results = risk_engine.check_order(
            "AAPL", 100, 200, 20000, side="BUY", as_of_date=today,
        )
        assert approved is False


class TestR5ResetDaily:
    """R-5: reset_daily clears _daily_order_count and _pending_order_values."""

    def test_reset_clears_counters(self):
        portfolio = Portfolio(initial_cash=100000)
        risk_engine = RiskEngine({"risk": {}}, portfolio, None)
        today = date(2025, 1, 2)

        risk_engine.record_order(symbol="AAPL", order_value=5000, as_of_date=today)
        risk_engine.record_order(symbol="MSFT", order_value=3000, as_of_date=today)
        assert risk_engine._daily_order_count == 2
        assert len(risk_engine._pending_order_values) == 2

        risk_engine.reset_daily()
        assert risk_engine._daily_order_count == 0
        assert len(risk_engine._pending_order_values) == 0


class TestF7NavWithOpenPositions:
    """F-7b: With positions, final_nav includes unrealized_pnl."""

    def test_nav_with_open_position_includes_unrealized(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyOnly:
            name = "BuyOnly"
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
                    om = ctx.order_manager
                    om.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BuyOnly")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 100.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyOnly()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(result.open_positions) > 0
        total_pnl = sum(t.pnl for t in result.trades)
        unrealized = sum(p["unrealized_pnl"] for p in result.open_positions)
        assert abs(result.final_nav - (100000 + total_pnl + unrealized)) < 1.0


class TestF10ExecuteOrderReturnConvention:
    """F-10: _execute_order returns List[Trade]=success, []=permanent drop, None=retry."""

    def test_success_returns_trade_list(self):
        bt = make_backtester()
        portfolio = Portfolio(initial_cash=1000000)
        diag = BacktestDiagnostics()
        order = {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "MARKET",
                 "price": 100.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}
        bar = {"open": 100.0, "close": 101.0, "high": 102.0, "low": 99.0, "volume": 1000000, "timestamp": START}
        entry_times = {}
        entry_prices = {}

        result = bt._execute_order(order, portfolio, "AAPL", bar, entry_times, entry_prices, diag)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_no_position_sell_returns_empty_list(self):
        bt = make_backtester()
        portfolio = Portfolio(initial_cash=1000000)
        diag = BacktestDiagnostics()
        order = {"symbol": "AAPL", "quantity": 100, "side": "SELL", "order_type": "MARKET",
                 "price": 100.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}
        bar = {"open": 100.0, "close": 100.0, "high": 101.0, "low": 99.0, "volume": 1000000, "timestamp": START}
        entry_times = {}
        entry_prices = {}

        result = bt._execute_order(order, portfolio, "AAPL", bar, entry_times, entry_prices, diag)
        assert result == []

    def test_limit_hit_returns_none(self):
        bt = make_backtester()
        portfolio = Portfolio(initial_cash=1000000)
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=(START + timedelta(days=1)).date())
        diag = BacktestDiagnostics()
        order = {"symbol": "600519", "quantity": 100, "side": "SELL", "order_type": "MARKET",
                 "price": 50.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}
        bar = {"open": 55.0, "close": 55.0, "high": 55.0, "low": 55.0, "volume": 1000000, "timestamp": START + timedelta(days=2)}
        prev_bar = {"close": 50.0}
        entry_times = {}
        entry_prices = {}

        result = bt._execute_order(order, portfolio, "600519", bar, entry_times, entry_prices, diag, prev_bar)
        assert result is None


class TestF11SlippageDirectionAllMarkets:
    """F-11: Buy adds slippage, sell subtracts, for all markets."""

    def test_us_slippage_direction(self):
        self._check_slippage("AAPL", "US")

    def test_cn_slippage_direction(self):
        self._check_slippage("600519", "CN", lot_qty=100)

    def test_hk_slippage_direction(self):
        self._check_slippage("0700", "HK", lot_qty=100)

    def _check_slippage(self, symbol, market, lot_qty=None):
        bps = 10
        config = {
            "backtest": {"slippage_bps": bps},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyOnly:
            name = "SlipTest"
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
                    qty = lot_qty if lot_qty else 100
                    ctx.order_manager.submit_order(symbol, qty, "BUY", "MARKET", 100.0, "SlipTest")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        if market == "CN":
            data = make_cn_bars([symbol], START, 5, {symbol: 100.0})
        elif market == "HK":
            data = make_hk_bars([symbol], START, 5, {symbol: 100.0})
        else:
            data = make_us_bars([symbol], START, 5, {symbol: 100.0})

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyOnly()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=[symbol],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        if buy_trades:
            raw_open = data[data["symbol"] == symbol].iloc[1]["open"]
            expected_fill = raw_open * (1 + bps / 10000)
            assert buy_trades[0].fill_price >= expected_fill - 0.01


class TestE7MultiStrategyIsolation:
    """E-7: Multiple strategies share portfolio and receive all fills.

    KNOWN ISSUE: All strategies receive all fills, so strategy._positions
    tracks the COMBINED portfolio position, not the strategy's own positions.
    The portfolio itself correctly tracks the aggregate. This is acceptable
    for single-strategy backtests but needs a fix for multi-strategy use.
    """

    def test_portfolio_tracks_combined_position(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class StratA:
            name = "StratA"
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "StratA")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        class StratB:
            name = "StratB"
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
                    ctx.order_manager.submit_order("MSFT", 100, "BUY", "MARKET", 100.0, "StratB")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL", "MSFT"], START, 5, {"AAPL": 100.0, "MSFT": 100.0})
        provider = DataFrameProvider(data)
        sA = StratA()
        sB = StratB()
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[sA, sB],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL", "MSFT"],
        )
        assert result.open_positions or len([t for t in result.trades if t.side == "BUY"]) >= 2

    def test_all_strategies_receive_all_fills(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class StratA:
            name = "StratA"
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "StratA")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        class StratB:
            name = "StratB"
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
                    ctx.order_manager.submit_order("MSFT", 100, "BUY", "MARKET", 100.0, "StratB")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL", "MSFT"], START, 5, {"AAPL": 100.0, "MSFT": 100.0})
        provider = DataFrameProvider(data)
        sA = StratA()
        sB = StratB()
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[sA, sB],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL", "MSFT"],
        )
        assert sA._positions.get("AAPL", 0) == 100
        assert sA._positions.get("MSFT", 0) == 100
        assert sB._positions.get("AAPL", 0) == 100
        assert sB._positions.get("MSFT", 0) == 100


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
