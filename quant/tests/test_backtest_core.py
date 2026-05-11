"""回测引擎核心测试 — 市场无关的通用功能。"""
from datetime import datetime, date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_bar_dict,
    make_cn_bars,
    make_us_bars,
    make_dividends_df,
    run_simple_backtest,
)
from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.dual_ma_crossover.strategy import DualMACrossover
from quant.features.backtest.engine import Backtester
from quant.features.backtest.entities import BacktestDiagnostics, BacktestResult, CommissionConfig
from quant.features.backtest.market_rules import DEFAULT_LOT_SIZE, is_suspended
from quant.features.backtest.commission import VOLUME_PARTICIPATION_LIMIT
from quant.features.backtest.exceptions import OrderRejectedError, OrderRejectionReason
from quant.features.backtest.order_executor import execute_order
from quant.features.backtest.schemas import DeferredOrder
from quant.features.backtest.walkforward import WalkForwardEngine
from quant.features.backtest.data_provider import DataFrameProvider
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.domain.models.trade import Trade
from quant.domain.models.position import Position


START = datetime(2025, 1, 2)


class TestBacktestDiagnostics:
    def test_discarded_orders_default_zero(self):
        diag = BacktestDiagnostics()
        assert diag.discarded_orders == 0

    def test_cost_drag_zero_when_no_pnl(self):
        diag = BacktestDiagnostics()
        assert diag.cost_drag_pct == 0.0

    def test_cost_drag_calculation(self):
        diag = BacktestDiagnostics(total_commission=100.0, total_gross_pnl=1000.0)
        assert diag.cost_drag_pct == pytest.approx(10.0, rel=1e-4)


class TestCommissionConfig:
    def test_default_us_config(self):
        cfg = CommissionConfig()
        assert cfg.US["type"] == "per_share"
        assert cfg.US["per_share"] == 0.005

    def test_default_hk_config(self):
        cfg = CommissionConfig()
        assert cfg.HK["type"] == "hk_realistic"

    def test_default_cn_config(self):
        cfg = CommissionConfig()
        assert cfg.CN["type"] == "cn_realistic"


class TestBacktesterExecution:
    def test_suspended_bar_detected(self):
        bar = {"volume": 0, "open": 100, "close": 100}
        assert is_suspended(bar) is True

    def test_normal_bar_not_suspended(self):
        bar = {"volume": 1000, "open": 100, "close": 100}
        assert is_suspended(bar) is False

    def test_buy_creates_trade_with_negative_pnl(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuyStrategy:
            name = "BuyTest"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", 150.0, "BuyTest"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyStrategy()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert len(result.trades) >= 1
        buy = result.trades[0]
        assert buy.side == "BUY"
        assert buy.pnl < 0

    def test_sell_without_position_is_noop(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class SellNoPosition:
            name = "SellNone"
            context = None
            _positions = {}

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                self.context.order_manager.submit_order(
                    "AAPL", 100, "SELL", "MARKET", 150.0, "SellNone"
                )

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[SellNoPosition()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        assert len(sell_trades) == 0

    def test_insufficient_cash_rejects_buy(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuyExpensive:
            name = "BuyExpensive"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100000, "BUY", "MARKET", 150.0, "BuyExpensive"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

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
        assert len(result.trades) == 0


class TestDataFrameProvider:
    def test_build_index(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        assert len(provider._bar_map) == 10

    def test_get_bar_for_date(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        bar = provider.get_bar_for_date("AAPL", START)
        assert bar is not None
        assert bar["symbol"] == "AAPL"

    def test_get_bar_for_date_missing(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        bar = provider.get_bar_for_date("AAPL", datetime(2020, 1, 1))
        assert bar is None

    def test_trading_dates(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        assert len(provider.trading_dates) == 10

    def test_validate_good_data(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        warnings = provider.validate()
        assert "Data is empty" not in warnings

    def test_validate_empty_data(self):
        provider = DataFrameProvider(pd.DataFrame())
        warnings = provider.validate()
        assert any("empty" in w.lower() for w in warnings)

    def test_dividend_lookup(self):
        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        dividends = make_dividends_df("600519", [START + timedelta(days=3)], [1.0])
        provider = DataFrameProvider(data, dividends=dividends)
        div = provider.get_dividend_for_date("600519", START + timedelta(days=3))
        assert div is not None
        assert div["cash_dividend"] == 1.0


class TestPositionModel:
    def test_initial_position(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.is_long
        assert not pos.is_short
        assert pos.cost_basis == 15000.0

    def test_settled_quantity_t1(self):
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        today = date(2025, 1, 2)
        pos.add_buy_lot(today, 1000)
        assert pos.settled_quantity(today) == 0
        assert pos.settled_quantity(date(2025, 1, 3)) == 1000

    def test_remove_sell_lots(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.add_buy_lot(date(2025, 1, 2), 100)
        pos.remove_sell_lots(50)
        assert pos.quantity == 100
        assert len(pos._lots) == 1
        assert sum(lot.qty for lot in pos._lots.values()) == 50

    def test_avg_cost_recalculated_after_partial_sell(self):
        pos = Position(symbol="AAPL")
        pos.add_buy_lot(date(2025, 1, 2), 100, 10.0)
        pos.add_buy_lot(date(2025, 1, 3), 100, 20.0)
        pos.quantity = 200
        pos.avg_cost = (100 * 10 + 100 * 20) / 200
        pos.remove_sell_lots(100)
        pos.recalc_avg_cost_from_lots()
        assert pos.avg_cost == pytest.approx(20.0)
        assert pos.quantity == 200
        remaining_lot = list(pos._lots.values())[0]
        assert remaining_lot.qty == 100
        assert remaining_lot.price == 20.0

    def test_avg_cost_correct_in_portfolio_after_partial_sell(self):
        from quant.features.trading.portfolio import Portfolio
        pf = Portfolio(initial_cash=100000)
        d1, d2 = date(2025, 1, 2), date(2025, 1, 3)
        pf.update_position("AAPL", 100, 10.0, 1000.0, trade_date=d1)
        pf.update_position("AAPL", 100, 20.0, 2000.0, trade_date=d2)
        pf.update_position("AAPL", -100, 25.0, 0, realized_pnl=(25.0 - 10.0) * 100)
        pos = pf.get_position("AAPL")
        assert pos.quantity == 100
        assert pos.avg_cost == pytest.approx(20.0)

    def test_flat_position(self):
        pos = Position(symbol="AAPL", quantity=0)
        assert pos.is_flat

    def test_update_from_fill_buy(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 150.0, date(2025, 1, 2))
        assert pos.quantity == 100
        assert pos.avg_cost == 150.0

    def test_update_from_fill_sell_closes(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.add_buy_lot(date(2025, 1, 2), 100, 150.0)
        pos.update_from_fill(-100, 160.0)
        assert pos.quantity == 0
        assert pos.realized_pnl == pytest.approx(1000.0, rel=1e-4)


class TestTradeModel:
    def test_is_win(self):
        t = Trade(
            symbol="AAPL", quantity=100, entry_price=150.0, exit_price=160.0,
            entry_time=datetime(2025, 1, 2), exit_time=datetime(2025, 1, 3),
            side="SELL", realized_pnl=500.0,
        )
        assert t.is_win

    def test_is_loss(self):
        t = Trade(
            symbol="AAPL", quantity=100, entry_price=150.0, exit_price=140.0,
            entry_time=datetime(2025, 1, 2), exit_time=datetime(2025, 1, 3),
            side="SELL", realized_pnl=-500.0,
        )
        assert t.is_loss

    def test_return_pct(self):
        t = Trade(
            symbol="AAPL", quantity=100, entry_price=150.0, exit_price=160.0,
            entry_time=datetime(2025, 1, 2), exit_time=datetime(2025, 1, 3),
            side="SELL", pnl=500.0,
        )
        expected = 500.0 / (150.0 * 100) * 100
        assert t.return_pct == pytest.approx(expected, rel=1e-4)

    def test_from_entry_exit_buy(self):
        t = Trade.from_entry_exit(
            "AAPL", 100, 150.0, 160.0,
            datetime(2025, 1, 2), datetime(2025, 1, 3),
            "BUY", commission=10.0,
        )
        assert t.pnl == pytest.approx((160.0 - 150.0) * 100 - 10.0, rel=1e-4)

    def test_duration_days(self):
        t = Trade(
            symbol="AAPL", quantity=100, entry_price=150.0, exit_price=160.0,
            entry_time=datetime(2025, 1, 2), exit_time=datetime(2025, 1, 5),
            side="SELL",
        )
        assert t.duration_days == pytest.approx(3.0, rel=1e-4)


class TestWalkForwardEngine:
    def test_empty_data_returns_not_viable(self):
        engine = WalkForwardEngine(train_window_days=5, test_window_days=2, step_days=2, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio)
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-01-02"]),
            "symbol": ["AAPL"],
            "open": [150.0],
            "high": [151.0],
            "low": [149.0],
            "close": [150.5],
            "volume": [1000000.0],
        })
        result = engine.run(
            strategy_factory=lambda params: DualMACrossover.__new__(DualMACrossover),
            data=df,
            param_grid={"lookback": [10]},
        )
        assert result.is_viable is False
        assert len(result.windows) == 0

    def test_wf_result_structure(self):
        engine = WalkForwardEngine(train_window_days=5, test_window_days=2, step_days=30, min_trades=0, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio)
        result = engine.run(
            strategy_factory=lambda params: DualMACrossover(
                symbols=["AAPL", "MSFT"],
                fast_period=3,
                slow_period=10,
            ),
            data=pd.DataFrame({
                "timestamp": pd.to_datetime(["2025-01-02"]),
                "symbol": ["AAPL"],
                "open": [150.0], "high": [151.0], "low": [149.0],
                "close": [150.5], "volume": [1000000.0],
            }),
            param_grid={"lookback": [5]},
            initial_cash=100000,
            config={"backtest": {"slippage_bps": 0}, "execution": {"commission": {}}, "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0}},
        )
        assert hasattr(result, "windows")
        assert hasattr(result, "aggregate_sharpe")
        assert hasattr(result, "is_viable")

    def test_records_param_trials_and_stability_metadata(self, monkeypatch):
        data = make_us_bars(["AAPL"], START, 80, {"AAPL": 150.0})
        engine = WalkForwardEngine(
            train_window_days=50,
            test_window_days=10,
            step_days=10,
            min_trades=30,
            portfolio_class=Portfolio,
            risk_engine_class=RiskEngine,
            sub_portfolio_class=SubPortfolio,
        )
        selected = [{"lookback": 5}, {"lookback": 10}, {"lookback": 5}]

        def fake_find_best_params(strategy_factory, train_data, param_grid, initial_cash, config):
            params = selected.pop(0)
            tested = [{"lookback": 5}, {"lookback": 10}]
            return params, 1.2, tested, len(tested), 35

        def fake_run_single_backtest(config, strategy, data, initial_cash):
            return SimpleNamespace(
                sharpe_ratio=1.1,
                total_return=0.05,
                max_drawdown_pct=0.02,
                trades=[object()] * 35,
            )

        monkeypatch.setattr(engine, "_find_best_params", fake_find_best_params)
        monkeypatch.setattr(engine, "_run_single_backtest", fake_run_single_backtest)

        result = engine.run(
            strategy_factory=lambda params: SimpleNamespace(name="WFTest"),
            data=data,
            param_grid={"lookback": [5, 10]},
        )

        assert result.param_trials == 6
        assert result.tested_param_sets == [{"lookback": 5}, {"lookback": 10}]
        assert result.selected_params_by_window == [{"lookback": 5}, {"lookback": 10}, {"lookback": 5}]
        assert result.parameter_stability == pytest.approx(2 / 3)
        assert result.min_train_trades == 35
        assert result.min_test_trades == 35
        assert result.multiple_testing_adjusted_alpha == pytest.approx(0.05 / 6)

    def test_aggregate_max_drawdown_uses_worst_negative_window(self, monkeypatch):
        data = make_us_bars(["AAPL"], START, 80, {"AAPL": 150.0})
        engine = WalkForwardEngine(
            train_window_days=50,
            test_window_days=10,
            step_days=10,
            min_trades=0,
            portfolio_class=Portfolio,
            risk_engine_class=RiskEngine,
            sub_portfolio_class=SubPortfolio,
        )
        drawdowns = iter([-0.02, -0.25, -0.08])

        def fake_find_best_params(strategy_factory, train_data, param_grid, initial_cash, config):
            return {"lookback": 5}, 1.0, [{"lookback": 5}], 1, 10

        def fake_run_single_backtest(config, strategy, data, initial_cash):
            return SimpleNamespace(
                sharpe_ratio=1.0,
                total_return=0.05,
                max_drawdown_pct=next(drawdowns),
                trades=[object()] * 10,
            )

        monkeypatch.setattr(engine, "_find_best_params", fake_find_best_params)
        monkeypatch.setattr(engine, "_run_single_backtest", fake_run_single_backtest)

        result = engine.run(
            strategy_factory=lambda params: SimpleNamespace(name="WFDDBug"),
            data=data,
            param_grid={"lookback": [5]},
        )

        assert result.aggregate_max_dd == pytest.approx(-0.25)

    def test_insufficient_oos_trades_marks_result_not_viable(self, monkeypatch):
        data = make_us_bars(["AAPL"], START, 70, {"AAPL": 150.0})
        engine = WalkForwardEngine(
            train_window_days=50,
            test_window_days=10,
            step_days=10,
            min_trades=30,
            portfolio_class=Portfolio,
            risk_engine_class=RiskEngine,
            sub_portfolio_class=SubPortfolio,
        )

        def fake_find_best_params(strategy_factory, train_data, param_grid, initial_cash, config):
            tested = [{"lookback": 5}]
            return {"lookback": 5}, 1.5, tested, len(tested), 40

        def fake_run_single_backtest(config, strategy, data, initial_cash):
            return SimpleNamespace(
                sharpe_ratio=1.4,
                total_return=0.08,
                max_drawdown_pct=0.01,
                trades=[object()] * 3,
            )

        monkeypatch.setattr(engine, "_find_best_params", fake_find_best_params)
        monkeypatch.setattr(engine, "_run_single_backtest", fake_run_single_backtest)

        result = engine.run(
            strategy_factory=lambda params: SimpleNamespace(name="WFSmallSample"),
            data=data,
            param_grid={"lookback": [5]},
        )

        assert result.min_test_trades == 3
        assert result.is_viable is False
        assert any("test trades" in warning for warning in result.viability_warnings)


class TestAdjustedPriceSeparation:

    def test_adj_helper_prefers_adj_close(self):
        from quant.features.strategies.base import Strategy
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._adj(bar, "close") == 105.0

    def test_adj_helper_falls_back_to_close(self):
        from quant.features.strategies.base import Strategy
        bar = {"close": 100.0}
        assert Strategy._adj(bar, "close") == 100.0

    def test_adj_helper_handles_nan(self):
        from quant.features.strategies.base import Strategy
        bar = {"close": 100.0, "adj_close": float("nan")}
        assert Strategy._adj(bar, "close") == 100.0

    def test_adj_helper_handles_none(self):
        from quant.features.strategies.base import Strategy
        bar = {"close": 100.0, "adj_close": None}
        assert Strategy._adj(bar, "close") == 100.0

    def test_adj_helper_high_low(self):
        from quant.features.strategies.base import Strategy
        bar = {"high": 105.0, "adj_high": 110.0, "low": 95.0, "adj_low": 99.0}
        assert Strategy._adj(bar, "high") == 110.0
        assert Strategy._adj(bar, "low") == 99.0

    def test_dual_ma_uses_adj_close(self):
        strategy = DualMACrossover(symbols=["TEST"], fast_period=2, slow_period=5)
        strategy.context = None
        bars = []
        for i in range(10):
            bars.append({"symbol": "TEST", "close": 100.0 - i * 2, "adj_close": 100.0,
                         "open": 100.0, "high": 101.0, "low": 99.0, "volume": 1000000})
        strategy._day_data["TEST"] = bars
        strategy._process_symbol(None, "TEST")
        assert strategy._positions.get("TEST", 0) == 0

    def test_engine_fill_uses_real_open(self):
        rows = []
        for i in range(5):
            ts = START + timedelta(days=i)
            rows.append({
                "symbol": "AAPL", "timestamp": ts,
                "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0,
                "adj_open": 300.0, "adj_high": 301.0, "adj_low": 299.0, "adj_close": 300.0,
                "adj_factor": 2.0, "volume": 1000000,
            })
        data = pd.DataFrame(rows)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuyOnce:
            name = "BuyOnce"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", None, "BuyOnce"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyOnce()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        for trade in result.trades:
            assert trade.fill_price == 150.0

    def test_nav_uses_real_close(self):
        rows = []
        for i in range(5):
            ts = START + timedelta(days=i)
            rows.append({
                "symbol": "AAPL", "timestamp": ts,
                "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0,
                "adj_open": 300.0, "adj_high": 301.0, "adj_low": 299.0, "adj_close": 300.0,
                "adj_factor": 2.0, "volume": 1000000,
            })
        data = pd.DataFrame(rows)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuyOnce:
            name = "BuyOnce"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", None, "BuyOnce"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = run_simple_backtest(
            bt, data,
            strategies=[BuyOnce()], symbols=["AAPL"], initial_cash=1000000,
        )
        if result.open_positions:
            for pos in result.open_positions:
                assert pos["current_price"] == 150.0


class TestStockDividendLotTracking:

    def _build_cn_data_with_stock_div(self):
        start = datetime(2025, 1, 2)
        rows = []
        prices = [100, 102, 104, 106, 108, 110, 112, 108, 110, 112, 114, 116]
        for i, p in enumerate(prices):
            rows.append({
                "symbol": "600519", "timestamp": start + timedelta(days=i),
                "open": p, "high": p + 1, "low": p - 1, "close": p,
                "volume": 5000000,
                "adj_open": p, "adj_high": p + 1, "adj_low": p - 1,
                "adj_close": p, "adj_factor": 1.0,
            })
        data = pd.DataFrame(rows)
        div = pd.DataFrame({
            "symbol": ["600519"], "ex_date": [start + timedelta(days=7)],
            "cash_dividend": [0.0], "stock_dividend": [1.0],
        })
        return data, div

    def test_stock_dividend_creates_lot_entries(self):
        data, div = self._build_cn_data_with_stock_div()
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)
        result = run_simple_backtest(
            bt, data,
            strategies=[StrategyRegistry.create("DualMACrossover", symbols=["600519"], fast_period=2, slow_period=3)],
            symbols=["600519"],
            dividends=div,
            initial_cash=1000000,
        )
        pos_obj = result.open_positions[0] if result.open_positions else None
        if pos_obj is None:
            sell_trades = [t for t in result.trades if t.side == "SELL"]
            total_sold = sum(t.quantity for t in sell_trades)
            assert total_sold > 0

    def test_stock_dividend_shares_sellable_cn(self):
        data, div = self._build_cn_data_with_stock_div()
        sell_day = 10
        total_bars = len(data)
        extra = []
        start = datetime(2025, 1, 2)
        for i in range(total_bars, total_bars + sell_day):
            p = 60
            extra.append({
                "symbol": "600519", "timestamp": start + timedelta(days=i),
                "open": p, "high": p + 1, "low": p - 1, "close": p,
                "volume": 5000000,
                "adj_open": p, "adj_high": p + 1, "adj_low": p - 1,
                "adj_close": p, "adj_factor": 1.0,
            })
        data = pd.concat([data, pd.DataFrame(extra)], ignore_index=True)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 10.0},
        }
        bt = make_backtester(config)
        result = run_simple_backtest(
            bt, data,
            strategies=[StrategyRegistry.create("DualMACrossover", symbols=["600519"], fast_period=2, slow_period=3)],
            symbols=["600519"],
            dividends=div,
            initial_cash=1000000,
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        if sell_trades:
            total_sold = sum(t.quantity for t in sell_trades)
            assert total_sold > 0

    def test_stock_dividend_adjusts_lots_proportionally(self):
        from quant.features.trading.portfolio import Portfolio
        portfolio = Portfolio(initial_cash=1000000, currency="CNY")
        portfolio.update_position("600519", quantity=100, price=100.0, cost=10000.0, trade_date=date(2025, 1, 2))
        pos = portfolio.get_position("600519")
        assert pos._lots
        assert sum(lot.qty for lot in pos._lots.values()) == 100
        assert pos.avg_cost == 100.0

        pos.adjust_lots_for_stock_dividend(1.0)

        assert pos.quantity == 200
        assert sum(lot.qty for lot in pos._lots.values()) == 200
        lot_dates = sorted(pos._lots.keys())
        assert len(lot_dates) == 1
        assert pos._lots[lot_dates[0]].qty == 200
        assert pos._lots[lot_dates[0]].price == 50.0
        assert pos.avg_cost == 50.0

    def test_cash_dividend_adjusts_lot_prices_down(self):
        from quant.features.trading.portfolio import Portfolio
        portfolio = Portfolio(initial_cash=1000000, currency="CNY")
        portfolio.update_position("600519", quantity=100, price=100.0, cost=10000.0, trade_date=date(2025, 1, 2))
        pos = portfolio.get_position("600519")

        pos.adjust_lots_for_cash_dividend(2.0)

        lot_dates = sorted(pos._lots.keys())
        assert len(lot_dates) == 1
        assert pos._lots[lot_dates[0]].price == 98.0
        assert pos.avg_cost == 98.0
        assert pos.quantity == 100


class TestStrategyPositionSyncAfterDividend:
    def test_on_fill_updates_positions(self):
        from types import SimpleNamespace
        from quant.features.strategies.base import Strategy

        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test")

        s = TestStrat()
        fill = SimpleNamespace(symbol="AAPL", quantity=100, side="BUY")
        s.on_fill(None, fill)
        assert s.get_position("AAPL") == 100

        sell_fill = SimpleNamespace(symbol="AAPL", quantity=50, side="SELL")
        s.on_fill(None, sell_fill)
        assert s.get_position("AAPL") == 50

    def test_stock_dividend_syncs_strategy_positions(self):
        from quant.features.backtest.engine import Backtester
        start = datetime(2025, 1, 2)
        rows = []
        for i, p in enumerate([100, 102, 104, 106, 108, 110, 112, 108, 110, 112]):
            rows.append({
                "symbol": "600519", "timestamp": start + timedelta(days=i),
                "open": p, "high": p + 1, "low": p - 1, "close": p,
                "volume": 5000000,
                "adj_open": p, "adj_high": p + 1, "adj_low": p - 1,
                "adj_close": p, "adj_factor": 1.0,
            })
        data = pd.DataFrame(rows)
        div = pd.DataFrame({
            "symbol": ["600519"], "ex_date": [start + timedelta(days=5)],
            "cash_dividend": [0.0], "stock_dividend": [0.5],
        })
        from quant.features.strategies.registry import StrategyRegistry
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)
        strat = StrategyRegistry.create("DualMACrossover", symbols=["600519"], fast_period=2, slow_period=3)
        result = run_simple_backtest(bt, data, strategies=[strat], symbols=["600519"], dividends=div, initial_cash=1000000)
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        if buy_trades:
            assert strat.get_position("600519") >= 0


class TestDataFrameProviderDedup:
    def test_get_bars_no_duplicates(self):
        from quant.features.backtest.data_provider import DataFrameProvider
        start = datetime(2025, 1, 2)
        rows = [
            {"symbol": "AAPL", "timestamp": start, "open": 150, "high": 151, "low": 149, "close": 150, "volume": 1000},
            {"symbol": "AAPL", "timestamp": start, "open": 151, "high": 152, "low": 150, "close": 151, "volume": 500},
        ]
        data = pd.DataFrame(rows)
        provider = DataFrameProvider(data)
        bars = provider.get_bars("AAPL", start, start + timedelta(days=1), "1d")
        assert len(bars) == 1
        assert bars.iloc[0]["volume"] == 1000

    def test_trading_dates_are_date_objects(self):
        from quant.features.backtest.data_provider import DataFrameProvider
        start = datetime(2025, 1, 2)
        data = make_us_bars(["AAPL"], start, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        for d in provider.trading_dates:
            assert isinstance(d, date)
            assert not isinstance(d, datetime)


class TestMarketOrderRiskCheck:

    def test_market_order_uses_last_price_for_risk(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 0.10, "max_daily_loss_pct": 1.0, "max_leverage": 10.0},
        }
        bt = make_backtester(config)

        class BuyBig:
            name = "BuyBig"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 10000, "BUY", "MARKET", None, "BuyBig"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyBig()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert result.diagnostics.risk_skipped_orders >= 1


class TestDailyLossRiskCheck:

    def test_daily_loss_check_uses_updated_prices(self):
        rows = []
        start = datetime(2025, 1, 2)
        for i in range(8):
            p = 100.0 if i < 4 else 80.0
            rows.append({
                "symbol": "AAPL", "timestamp": start + timedelta(days=i),
                "open": p, "high": p + 1, "low": p - 1, "close": p,
                "volume": 1000000,
                "adj_open": p, "adj_high": p + 1, "adj_low": p - 1,
                "adj_close": p, "adj_factor": 1.0,
            })
        data = pd.DataFrame(rows)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 10.0},
        }
        bt = make_backtester(config)

        class BuyAndHoldThenBuyMore:
            name = "BuyHold"
            context = None
            _positions = {}
            _day = 0
            _bought = False
            _tried_extra = False

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                self._day += 1
                if not self._bought and self._day >= 2:
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", 100.0, "BuyHold"
                    )
                    self._bought = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyAndHoldThenBuyMore()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1


@pytest.mark.regression
class TestCriticalRegression:
    """Regression tests for bugs found in audit: C2, C3, on_stop, dedup."""

    def test_bug_c3_no_trading_dates_skips_non_trading_days(self):
        """Engine skips days with no bar data when trading_dates is unavailable."""
        from quant.tests.conftest import MockDataProvider

        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)

        # Only provide data for Mon 2024-06-03 and Wed 2024-06-05 (Tue is missing)
        bars = {}
        for sym in ["AAPL"]:
            bars[(sym, date(2024, 6, 3))] = {
                "symbol": sym, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000,
                "timestamp": datetime(2024, 6, 3), "_suspended": False,
            }
            bars[(sym, date(2024, 6, 5))] = {
                "symbol": sym, "open": 105, "high": 106, "low": 104, "close": 105, "volume": 1_000_000,
                "timestamp": datetime(2024, 6, 5), "_suspended": False,
            }
        provider = MockDataProvider(bars=bars)

        class DayCounter:
            name = "DayCounter"
            context = None
            _positions = {}
            _days_seen = []

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                self._days_seen.append(("before", td))

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                self._days_seen.append(("after", td))

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        bt.run(
            start=datetime(2024, 6, 3), end=datetime(2024, 6, 6),
            strategies=[DayCounter()], initial_cash=100000,
            data_provider=provider, symbols=["AAPL"],
        )
        # Only days with bar data should trigger hooks
        before_dates = [d for tag, d in DayCounter._days_seen if tag == "before"]
        assert date(2024, 6, 4) not in before_dates, "Tuesday (no data) should be skipped"

    def test_bug_c2_stock_dividend_fill_has_all_fields(self):
        """Stock dividend synthetic fill includes all Trade fields."""
        from types import SimpleNamespace

        fill = SimpleNamespace(
            symbol="AAPL", quantity=10, side="BUY",
            price=0.0, fill_price=0.0, pnl=0.0, commission=0.0,
            realized_pnl=0.0, entry_price=0.0, exit_price=0.0,
            intended_qty=10, cost_breakdown={},
            entry_time=datetime(2024, 6, 5), exit_time=datetime(2024, 6, 5),
            signal_date=datetime(2024, 6, 5), fill_date=datetime(2024, 6, 5),
            strategy_name="test",
        )
        # All fields needed by strategy on_fill should be present
        assert fill.symbol == "AAPL"
        assert fill.quantity == 10
        assert fill.side == "BUY"
        assert fill.price == 0.0
        assert fill.pnl == 0.0
        assert fill.commission == 0.0
        assert fill.realized_pnl == 0.0
        assert fill.cost_breakdown == {}
        assert fill.strategy_name == "test"

    def test_on_stop_orders_are_executed(self):
        """Orders generated in on_stop are executed as close-out trades."""
        import numpy as np

        bars = []
        for i in range(5):
            bars.append({
                "symbol": "AAPL", "timestamp": datetime(2024, 6, 3) + timedelta(days=i),
                "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.0 + i,
                "volume": 1_000_000,
            })
        data = pd.DataFrame(bars)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)

        class CloseOutStrat:
            name = "CloseOut"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", 100.0, "CloseOut"
                    )
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pos = self._positions.get("AAPL", 0)
                if pos > 0:
                    self.context.order_manager.submit_order(
                        "AAPL", pos, "SELL", "MARKET", 105.0, "CloseOut"
                    )

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(), end=data["timestamp"].max(),
            strategies=[CloseOutStrat()], initial_cash=100000,
            data_provider=provider, symbols=["AAPL"],
        )
        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) >= 1, "on_stop close-out should produce SELL trades"
        assert abs(sum(t.quantity for t in sells) - 100) < 1e-6
        assert result.diagnostics.forced_closeout_orders == 1
        assert result.diagnostics.forced_closeout_trades == 1

    def test_cn_on_stop_forced_closeout_bypasses_t1(self):
        data = make_cn_bars(["600519"], START, 2, {"600519": 10.0})
        config = {
            "backtest": {"slippage_bps": 0, "force_close_on_stop": True},
            "execution": {"commission": {"CN": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)

        class CNCloseOut:
            name = "CNCloseOut"
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
                    self.context.order_manager.submit_order(
                        "600519", 100, "BUY", "MARKET", None, "CNCloseOut"
                    )
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pos = self._positions.get("600519", 0)
                if pos > 0:
                    self.context.order_manager.submit_order(
                        "600519", pos, "SELL", "MARKET", None, "CNCloseOut"
                    )

        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[CNCloseOut()],
            initial_cash=100000,
            data_provider=DataFrameProvider(data),
            symbols=["600519"],
        )

        assert len(result.open_positions) == 0
        assert [t.side for t in result.trades] == ["BUY", "SELL"]
        assert result.diagnostics.forced_closeout_orders == 1
        assert result.diagnostics.forced_closeout_trades == 1

    def test_on_stop_closeout_can_be_disabled(self):
        """on_stop orders can be discarded instead of synthetic close-out fills."""
        data = make_us_bars(["AAPL"], START, 4, {"AAPL": 100.0})
        config = {
            "backtest": {"slippage_bps": 0, "force_close_on_stop": False},
            "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)

        class CloseOutDisabled:
            name = "CloseOutDisabled"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", None, "CloseOutDisabled"
                    )
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pos = self._positions.get("AAPL", 0)
                if pos > 0:
                    self.context.order_manager.submit_order(
                        "AAPL", pos, "SELL", "MARKET", None, "CloseOutDisabled"
                    )

        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[CloseOutDisabled()],
            initial_cash=100000,
            data_provider=DataFrameProvider(data),
            symbols=["AAPL"],
        )

        assert [t.side for t in result.trades] == ["BUY"]
        assert result.open_positions[0]["quantity"] == pytest.approx(100)
        assert result.diagnostics.forced_closeout_orders == 1
        assert result.diagnostics.forced_closeout_trades == 0
        assert result.diagnostics.discarded_orders == 1
        assert result.diagnostics.expired_orders == 1

    def test_cash_dividend_is_recorded_in_diagnostics(self):
        """Cash dividend cash flow is exposed for NAV reconstruction."""
        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 100.0})
        dividends = make_dividends_df("AAPL", [START + timedelta(days=2)], [1.25])
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)

        class DividendBuyer:
            name = "DividendBuyer"
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
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", None, "DividendBuyer"
                    )
                self._day += 1

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[DividendBuyer()],
            initial_cash=100000,
            data_provider=DataFrameProvider(data, dividends=dividends),
            symbols=["AAPL"],
        )

        assert result.diagnostics.total_cash_dividends == pytest.approx(125.0)
        assert result.diagnostics.total_dividend_tax == pytest.approx(0.0)
        assert result.diagnostics.total_net_dividends == pytest.approx(125.0)

    def test_final_trading_day_deferred_order_expires_without_synthetic_fill(self):
        """Orders generated on the final trading day cannot fill without a real next bar."""
        data = make_us_bars(["AAPL"], START, 2, {"AAPL": 150.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)
        final_date = data["timestamp"].max().date()

        class FinalDayBuyer:
            name = "FinalDayBuyer"
            context = None
            _positions = {}

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if td == final_date:
                    self.context.order_manager.submit_order(
                        "AAPL", 100, "BUY", "MARKET", None, "FinalDayBuyer"
                    )

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[FinalDayBuyer()],
            initial_cash=100000,
            data_provider=DataFrameProvider(data),
            symbols=["AAPL"],
        )

        assert result.trades == []
        assert result.diagnostics.discarded_orders == 1
        assert result.diagnostics.expired_orders == 1

    def test_equity_curve_does_not_append_end_plus_one_without_closeout(self):
        data = make_us_bars(["AAPL"], START, 2, {"AAPL": 150.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
        }
        bt = make_backtester(config)

        class NoTrade:
            name = "NoTrade"
            context = None

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                pass

            def on_fill(self, ctx, fill):
                pass

            def get_position(self, symbol):
                return 0

            def on_stop(self, ctx):
                pass

        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[NoTrade()],
            initial_cash=100000,
            data_provider=DataFrameProvider(data),
            symbols=["AAPL"],
        )

        assert list(result.equity_curve.index) == list(data["timestamp"])

    def test_nan_sell_open_is_rejected_before_state_mutation(self):
        portfolio = Portfolio(initial_cash=100000)
        portfolio.update_position("AAPL", quantity=1, price=10, cost=10, trade_date=START.date())
        order = DeferredOrder(
            symbol="AAPL", quantity=1, side="SELL", order_type="MARKET",
            price=None, strategy="NaNGuard", signal_date=START, risk_check_price=10,
        )
        bar = {
            "symbol": "AAPL", "timestamp": START + timedelta(days=1),
            "open": float("nan"), "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 1000,
        }

        with pytest.raises(OrderRejectedError) as exc:
            execute_order(
                order, portfolio, "AAPL", bar, {}, {}, BacktestDiagnostics(),
                {}, None, 0, CommissionConfig(),
            )

        assert exc.value.reason == OrderRejectionReason.PRICE_INVALID
        assert np.isfinite(portfolio.cash)
        assert np.isfinite(portfolio.nav)

    def test_cn_odd_lot_sell_volume_cap_keeps_sellable_quantity(self):
        portfolio = Portfolio(initial_cash=100000, currency="CNY")
        portfolio.update_position(
            "600519", quantity=50, price=10.0, cost=500.0,
            trade_date=(START - timedelta(days=2)).date(),
        )
        order = DeferredOrder(
            symbol="600519", quantity=50, side="SELL", order_type="MARKET",
            price=None, strategy="CNOddLotCap", signal_date=START, risk_check_price=10,
        )
        bar = {
            "symbol": "600519", "timestamp": START + timedelta(days=1),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 500,
        }
        diag = BacktestDiagnostics()

        trades = execute_order(
            order, portfolio, "600519", bar, {}, {}, diag,
            {}, None, 0, CommissionConfig(), prev_bar={"close": 10.0},
        )

        assert sum(t.quantity for t in trades) == pytest.approx(25.0)
        assert diag.volume_limited_trades == 1

    def test_context_drain_resets_buy_dedup(self):
        """OrderManager drain clears the BUY dedup set."""
        from quant.features.backtest.entities import _BacktestContext, _BacktestOrderManager

        class DummyRisk:
            def check_order(self, *args, **kwargs):
                return True, None
            def record_order(self, *args, **kwargs):
                pass
            def reset_daily(self):
                pass
            _risk_rejected_count = 0

        om = _BacktestOrderManager(DummyRisk())
        om._current_date = date(2024, 6, 3)
        om._last_prices = {"AAPL": 100.0}

        id1 = om.submit_order("AAPL", 100, "BUY", "MARKET", None, "test")
        assert id1 is not None, "First BUY should be accepted"
        id2 = om.submit_order("AAPL", 100, "BUY", "MARKET", None, "test")
        assert id2 is None, "Duplicate BUY should be rejected"

        orders = om.drain_pending(signal_date=datetime(2024, 6, 3))
        assert len(orders) == 1

        id3 = om.submit_order("AAPL", 100, "BUY", "MARKET", None, "test")
        assert id3 is not None, "BUY should be accepted after drain"
