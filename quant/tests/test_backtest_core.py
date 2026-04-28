"""回测引擎核心测试 — 市场无关的通用功能。"""
from datetime import datetime, date, timedelta

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
from quant.features.backtest.engine import (
    Backtester,
    BacktestDiagnostics,
    BacktestResult,
    CommissionConfig,
    DEFAULT_LOT_SIZE,
    VOLUME_PARTICIPATION_LIMIT,
    MAX_FILL_DEFER_DAYS,
)
from quant.features.backtest.walkforward import DataFrameProvider, WalkForwardEngine
from quant.domain.models.trade import Trade
from quant.domain.models.position import Position


START = datetime(2025, 1, 2)


class TestBacktestDiagnostics:
    def test_avg_fill_delay_zero_when_no_fills(self):
        diag = BacktestDiagnostics()
        assert diag.avg_fill_delay_days == 0.0

    def test_avg_fill_delay_with_fills(self):
        diag = BacktestDiagnostics(total_fill_delay_days=10, fill_count=5)
        assert diag.avg_fill_delay_days == 2.0

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
        assert Backtester._is_suspended(bar, None) is True

    def test_normal_bar_not_suspended(self):
        bar = {"volume": 1000, "open": 100, "close": 100}
        assert Backtester._is_suspended(bar, None) is False

    def test_buy_creates_trade_with_negative_pnl(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

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
        bt = Backtester(config)

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
        bt = Backtester(config)

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
        assert "Data is empty" in warnings

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
        pos.add_buy_lot(date(2025, 1, 2), 100)
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
        engine = WalkForwardEngine(train_window_days=5, test_window_days=2, step_days=2)
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
            strategy_factory=lambda params: SimpleMomentum.__new__(SimpleMomentum),
            data=df,
            param_grid={"lookback": [10]},
        )
        assert result.is_viable is False
        assert len(result.windows) == 0

    def test_wf_result_structure(self):
        engine = WalkForwardEngine(train_window_days=5, test_window_days=2, step_days=30, min_trades=0)
        result = engine.run(
            strategy_factory=lambda params: SimpleMomentum(
                symbols=["AAPL", "MSFT"],
                momentum_lookback=5,
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

    def test_momentum_uses_adj_close(self):
        from quant.features.strategies.base import Strategy
        strategy = StrategyRegistry.create("SimpleMomentum", symbols=["TEST"], momentum_lookback=2)
        strategy.context = None
        bar_real_drop = {"symbol": "TEST", "close": 90.0, "adj_close": 101.0, "open": 90.0, "high": 91.0, "low": 89.0, "volume": 1000000}
        bar_earlier = {"symbol": "TEST", "close": 100.0, "adj_close": 100.0, "open": 99.0, "high": 101.0, "low": 99.0, "volume": 1000000}
        strategy._day_data["TEST"] = [bar_earlier, bar_real_drop]
        strategy._calculate_momentum_scores()
        score = strategy._momentum_scores.get("TEST", 0)
        assert score > 0

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
        bt = Backtester(config)
        result = run_simple_backtest(
            bt, data,
            strategies=[StrategyRegistry.create("SimpleMomentum", symbols=["AAPL"], momentum_lookback=2, holding_period=1)],
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
        bt = Backtester(config)
        result = run_simple_backtest(
            bt, data,
            strategies=[StrategyRegistry.create("SimpleMomentum", symbols=["AAPL"], momentum_lookback=2, holding_period=1)],
            symbols=["AAPL"],
            initial_cash=1000000,
        )
        if result.open_positions:
            for pos in result.open_positions:
                assert pos["current_price"] == 150.0
