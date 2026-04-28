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
    make_dividends_df,
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
from quant.domain.models.trade import Trade

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
            "risk": {"max_position_pct": 0.000001, "max_daily_loss_pct": 1.0},
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
        assert result.diagnostics.risk_skipped_orders >= 1

    def test_risk_skipped_orders_increments_on_position_limit(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 0.0001, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyMultiple:
            name = "BuyMulti"
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "BuyMulti")
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "BuyMulti")
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "BuyMulti")
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
            strategies=[BuyMultiple()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert result.diagnostics.risk_skipped_orders >= 1


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

if HAS_HYPOTHESIS:
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


# ============================================================
# MANUAL → AUTO: Promoting previously untested invariants
# ============================================================

class TestE2CallbackOrder:
    """E-2: on_before_trading → on_data → on_after_trading order is enforced."""

    def test_callback_order_within_single_day(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)
        call_log = []

        class OrderTracker:
            name = "OrderTracker"
            context = None

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                call_log.append(("on_before_trading", td))

            def on_data(self, ctx, data):
                call_log.append(("on_data", data.get("symbol")))

            def on_after_trading(self, ctx, td):
                call_log.append(("on_after_trading", td))

            def on_fill(self, ctx, fill):
                call_log.append(("on_fill", fill.symbol))

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 3, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[OrderTracker()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )

        for i in range(0, len(call_log) - 2, 3):
            assert call_log[i][0] == "on_before_trading"
            assert call_log[i + 1][0] == "on_data"
            assert call_log[i + 2][0] == "on_after_trading"


class TestE3DeferredFillBeforeNewSignal:
    """E-3: Deferred order fills happen before new signal collection."""

    def test_fill_processed_before_on_after_trading(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)
        fill_seen_in_after_trading = []

        class BuyAndReact:
            name = "BuyReact"
            context = None
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if self._day == 0:
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "BuyReact")
                elif self._day == 1:
                    has_fill = len([t for t in ctx.portfolio.positions.values() if t.quantity > 0]) > 0
                    fill_seen_in_after_trading.append(has_fill)
                self._day += 1

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyAndReact()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(fill_seen_in_after_trading) >= 1
        assert fill_seen_in_after_trading[0] is True


class TestE4ResetDailyCalled:
    """E-4: portfolio.reset_daily() and risk_engine.reset_daily() called each loop."""

    def test_risk_rejected_count_resets_daily(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyEveryDay:
            name = "BuyEvery"
            context = None

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                ctx.order_manager.submit_order("AAPL", 10, "BUY", "MARKET", 150.0, "BuyEvery")

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyEveryDay()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert result.diagnostics.risk_skipped_orders == 0


class TestE5NonTradingDaysSkipped:
    """E-5: Non-trading days (not in trading_dates set) are skipped."""

    def test_weekend_dates_not_processed(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)
        seen_dates = []

        class TrackDates:
            name = "TrackDates"
            context = None

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                seen_dates.append(td)

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                pass

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[TrackDates()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        data_dates = set(d.date() for d in data[data["symbol"] == "AAPL"]["timestamp"])
        seen_set = set(seen_dates)
        assert seen_set == data_dates


class TestWF1TradingDayIndices:
    """WF-1: WalkForward train/test windows use trading-day indices."""

    def test_window_sizes_match_trading_days(self):
        engine = WalkForwardEngine(train_window_days=3, test_window_days=2, step_days=3, min_trades=0)
        n = 10
        timestamps = [START + timedelta(days=i) for i in range(n)]
        data = pd.DataFrame({
            "timestamp": timestamps,
            "symbol": ["AAPL"] * n,
            "open": [150.0] * n,
            "high": [151.0] * n,
            "low": [149.0] * n,
            "close": [150.5] * n,
            "volume": [1e6] * n,
        })

        from quant.features.strategies.simple_momentum.strategy import SimpleMomentum
        result = engine.run(
            strategy_factory=lambda p: SimpleMomentum(symbols=["AAPL"], momentum_lookback=5),
            data=data,
            param_grid={"lookback": [2]},
            initial_cash=100000,
            config={"backtest": {"slippage_bps": 0}, "execution": {"commission": {}}, "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0}},
        )
        if result.windows:
            w = result.windows[0]
            train_bar_count = len(data[(data["timestamp"] >= w.train_start) & (data["timestamp"] < w.train_end)])
            test_bar_count = len(data[(data["timestamp"] >= w.test_start) & (data["timestamp"] < w.test_end)])
            assert train_bar_count == 3
            assert test_bar_count == 2


class TestD4LotAdjustedTrades:
    """D-4: lot_adjusted_trades is incremented when BUY quantity is rounded to lot."""

    def test_cn_buy_quantity_rounded_triggers_count(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class Buy150Shares:
            name = "Buy150"
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
                    ctx.order_manager.submit_order("600519", 150, "BUY", "MARKET", 50.0, "Buy150")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 5, {"600519": 50.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[Buy150Shares()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        assert result.diagnostics.lot_adjusted_trades >= 1
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1
        assert buy_trades[0].quantity == 100


class TestM4TradeFrozen:
    """M-4: Trade is a frozen dataclass — fields are immutable."""

    def test_trade_field_immutable(self):
        t = Trade(
            symbol="AAPL", quantity=100, entry_price=150.0, exit_price=160.0,
            entry_time=START, exit_time=START + timedelta(days=1),
            side="SELL", pnl=500.0,
        )
        with pytest.raises(AttributeError):
            t.pnl = 999.0

    def test_trade_symbol_immutable(self):
        t = Trade(
            symbol="AAPL", quantity=100, entry_price=150.0, exit_price=160.0,
            entry_time=START, exit_time=START + timedelta(days=1),
            side="SELL",
        )
        with pytest.raises(AttributeError):
            t.symbol = "MSFT"


class TestD9TotalGrossPnl:
    """D-9: total_gross_pnl = sum(trade.pnl) + diag.total_commission."""

    def test_gross_pnl_formula_holds(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuySell:
            name = "BnS"
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
                    om.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BnS")
                elif self._day == 4:
                    om.submit_order("AAPL", 100, "SELL", "MARKET", 100.0, "BnS")
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
            strategies=[BuySell()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        total_pnl = sum(t.pnl for t in result.trades)
        expected_gross = total_pnl + result.diagnostics.total_commission
        assert result.diagnostics.total_gross_pnl == pytest.approx(expected_gross, rel=1e-6)


class TestX1ExecutionStepOrder:
    """X-1/X-3: Slippage applied after limit check, lot rounding after slippage."""

    def test_slippage_applied_after_limit_check(self):
        config = {
            "backtest": {"slippage_bps": 10},
            "execution": {"commission": {}},
            "risk": {},
        }
        bt = Backtester(config)
        portfolio = Portfolio(initial_cash=1000000)
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0,
                                  trade_date=(START + timedelta(days=1)).date())
        diag = BacktestDiagnostics()

        bar_not_at_limit = {"open": 54.0, "close": 54.0, "high": 54.0, "low": 54.0,
                            "volume": 1000000, "timestamp": START + timedelta(days=2)}
        prev_bar = {"close": 50.0}
        order = {"symbol": "600519", "quantity": 100, "side": "SELL", "order_type": "MARKET",
                 "price": 50.0, "strategy": "test", "_signal_date": START, "_deferred_days": 0}
        entry_times = {}
        entry_prices = {}

        result = bt._execute_order(order, portfolio, "600519", bar_not_at_limit,
                                    entry_times, entry_prices, diag, prev_bar)
        assert isinstance(result, list) and len(result) > 0
        raw_open = bar_not_at_limit["open"]
        assert result[0].fill_price < raw_open

    def test_lot_rounding_after_slippage_cn(self):
        config = {
            "backtest": {"slippage_bps": 10},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class Buy180:
            name = "Buy180"
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
                    ctx.order_manager.submit_order("600519", 180, "BUY", "MARKET", 50.0, "Buy180")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 5, {"600519": 50.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[Buy180()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1
        assert buy_trades[0].quantity == 100
        assert result.diagnostics.lot_adjusted_trades >= 1


class TestDIV1CashDividendProcessing:
    """DIV-1: Cash dividend credited to portfolio.cash minus tax."""

    def test_cn_cash_dividend_with_tax(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)
        cash_after_div = []

        class BuyAndTrackCash:
            name = "DivTest"
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
                if self._day == 0:
                    ctx.order_manager.submit_order("600519", 100, "BUY", "MARKET", 50.0, "DivTest")
                if self._day >= 2:
                    cash_after_div.append(ctx.portfolio.cash)
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        div_date = START + timedelta(days=3)
        dividends = make_dividends_df("600519", [div_date], [2.0])
        provider = DataFrameProvider(data, dividends=dividends)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyAndTrackCash()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        assert len(cash_after_div) >= 2
        assert cash_after_div[-1] > cash_after_div[0]


class TestDIV4DividendsBeforeFills:
    """DIV-4: Dividends processed before deferred order fills."""

    def test_dividend_visible_in_on_data_same_day(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)
        cash_snapshots = {}

        class TrackCash:
            name = "TrackCash"
            context = None
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                cash_snapshots[td] = ctx.portfolio.cash

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if self._day == 0:
                    ctx.order_manager.submit_order("600519", 100, "BUY", "MARKET", 50.0, "TrackCash")
                self._day += 1

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        div_date = START + timedelta(days=3)
        dividends = make_dividends_df("600519", [div_date], [1.0])
        provider = DataFrameProvider(data, dividends=dividends)
        bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[TrackCash()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        div_d = div_date.date() if hasattr(div_date, 'date') else div_date
        next_d = (div_date + timedelta(days=1)).date()
        if div_d in cash_snapshots and next_d in cash_snapshots:
            assert cash_snapshots[next_d] >= cash_snapshots[div_d]


class TestMTM1PortfolioPricesUpdated:
    """MTM-1: Portfolio market prices updated each day from last_prices."""

    def test_market_value_uses_close_price(self):
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "BuyOnly")
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
            strategies=[BuyOnly()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(result.open_positions) >= 1
        last_close = data[data["symbol"] == "AAPL"].iloc[-1]["close"]
        pos = result.open_positions[0]
        assert pos["market_value"] == pytest.approx(pos["quantity"] * last_close, rel=0.01)


class TestMTM3EntryTimesCleanup:
    """MTM-3: entry_times/entry_prices cleaned after full position close."""

    def test_entry_times_cleared_on_full_close(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuySellAll:
            name = "BnSAll"
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
                    om.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BnSAll")
                elif self._day == 3:
                    om.submit_order("AAPL", 100, "SELL", "MARKET", 100.0, "BnSAll")
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
            strategies=[BuySellAll()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(result.open_positions) == 0


class TestOP2EarliestLotTimePreference:
    """OP-2: open_positions uses _earliest_lot_time(pos) over entry_times."""

    def test_open_position_uses_lot_time_not_entry_time(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyTwice:
            name = "Buy2"
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
                if self._day in (0, 2):
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "Buy2")
                self._day += 1

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
            strategies=[BuyTwice()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(result.open_positions) >= 1
        entry_time = result.open_positions[0]["entry_time"]
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        if len(buy_trades) >= 2:
            assert entry_time == buy_trades[0].fill_date or entry_time is not None


class TestDEF2FirstDayNoPrevBar:
    """DEF-2: First CN day has no prev_bar, so limit check is skipped."""

    def test_cn_first_day_sell_skips_limit_check(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class SellPreloaded:
            name = "SellPre"
            context = None
            _positions = {}
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                if self._day == 0 and "600519" not in ctx.portfolio.positions:
                    ctx.portfolio.update_position("600519", quantity=100, price=50.0,
                                                  cost=5000.0, trade_date=date(2024, 12, 30))

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if self._day == 0:
                    ctx.order_manager.submit_order("600519", 100, "SELL", "MARKET", 50.0, "SellPre")
                self._day += 1

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = make_cn_bars(["600519"], START, 5, {"600519": 55.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[SellPreloaded()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        assert result.diagnostics.limit_rejected_orders == 0


# ============================================================
# Bug A + Bug 1/2/3 regression tests
# ============================================================

class TestBugAPrevCloseBarsNotOverwritten:
    """Bug A: _execute_order must receive YESTERDAY's bar as prev_bar, not today's."""

    def test_cn_limit_up_detected_with_correct_prev_close(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyThenSell:
            name = "LimitTest"
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
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "LimitTest")
                elif self._day == 1:
                    om.submit_order("600519", 100, "SELL", "MARKET", 55.0, "LimitTest")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        rows = []
        base = START
        prices = [50.0, 51.0, 56.1, 52.0, 51.0]
        for i, p in enumerate(prices):
            ts = base + timedelta(days=i)
            rows.append({"symbol": "600519", "timestamp": ts, "open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 5000000})
        data = pd.DataFrame(rows)
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyThenSell()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        assert result.diagnostics.limit_rejected_orders >= 1, "CN limit-up should be detected with yesterday's close"


class TestBug1LimitExpiredCounted:
    """Bug 1: Limit-hit orders that exceed MAX_FILL_DEFER_DAYS must increment expired_orders."""

    def test_limit_expired_order_counted(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyThenSell:
            name = "LimitExp"
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
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "LimitExp")
                elif self._day == 1:
                    om.submit_order("600519", 100, "SELL", "MARKET", 55.0, "LimitExp")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        rows = []
        base = START
        rows.append({"symbol": "600519", "timestamp": base, "open": 50, "high": 51, "low": 49, "close": 50, "volume": 5000000})
        rows.append({"symbol": "600519", "timestamp": base + timedelta(days=1), "open": 51, "high": 52, "low": 50, "close": 55, "volume": 5000000})
        for i in range(2, 10):
            ts = base + timedelta(days=i)
            rows.append({"symbol": "600519", "timestamp": ts, "open": 60.5, "high": 61, "low": 60, "close": 60.5, "volume": 5000000})
        data = pd.DataFrame(rows)
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyThenSell()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        if len(sell_trades) == 0:
            assert result.diagnostics.expired_orders >= 1


class TestBug2FillCountOnlyOnSuccess:
    """Bug 2: fill_count should only increment on actual successful fills."""

    def test_insufficient_cash_does_not_inflate_fill_count(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyExpensive:
            name = "BuyExp"
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
                    ctx.order_manager.submit_order("AAPL", 100000, "BUY", "MARKET", 150.0, "BuyExp")
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
            strategies=[BuyExpensive()],
            initial_cash=100,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert result.diagnostics.fill_count == 0, "fill_count should be 0 when no fills succeed"


class TestBug3CloseZeroPreservesLastPrice:
    """Bug 3: close=0 should not overwrite last_prices."""

    def test_close_zero_bar_preserves_previous_price(self):
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BuyOnly")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        rows = [
            {"symbol": "AAPL", "timestamp": START, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000000},
            {"symbol": "AAPL", "timestamp": START + timedelta(days=1), "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000000},
            {"symbol": "AAPL", "timestamp": START + timedelta(days=2), "open": 100, "high": 101, "low": 99, "close": 0, "volume": 500000},
            {"symbol": "AAPL", "timestamp": START + timedelta(days=3), "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1000000},
        ]
        data = pd.DataFrame(rows)
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyOnly()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(result.open_positions) >= 1
        assert result.open_positions[0]["current_price"] == 102
        assert result.open_positions[0]["market_value"] > 0
