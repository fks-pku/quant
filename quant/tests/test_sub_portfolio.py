"""Tests for SubPortfolio virtual sub-account isolation."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from quant.features.trading.portfolio import Portfolio
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.features.backtest.engine import Backtester
from quant.features.backtest.data_provider import DataFrameProvider
from quant.tests.conftest import make_backtester


START = datetime(2025, 1, 2)


class TestSubPortfolioUnit:

    def test_initial_state(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        assert sub.cash == 40000
        assert sub.allocated_capital == 40000
        assert sub.nav == 40000
        assert sub.strategy_name == "test"
        assert master.cash == 60000

    def test_buy_depletes_sub_cash_only(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.cash -= 10000
        assert sub.cash == 30000
        assert master.cash == 60000

    def test_sell_replenishes_sub_cash_only(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.cash -= 10000
        sub.cash += 12000
        assert sub.cash == 42000
        assert master.cash == 60000

    def test_can_afford_checks_sub_allocation(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        assert sub.can_afford(40000) is True
        assert sub.can_afford(40001) is False

    def test_can_afford_sub_isolation_from_master(self):
        master = Portfolio(initial_cash=100000)
        subA = SubPortfolio("A", 60000, master)
        subB = SubPortfolio("B", 40000, master)
        subA.cash -= 60000
        assert master.cash == 0
        assert subB.cash == 40000
        assert subB.can_afford(35000) is True

    def test_update_position_buy(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, trade_date=date(2025, 1, 2))
        pos = sub.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == 150.0
        assert pos.market_value == 15000.0

    def test_positions_are_isolated(self):
        master = Portfolio(initial_cash=100000)
        subA = SubPortfolio("A", 50000, master)
        subB = SubPortfolio("B", 50000, master)
        subA.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, trade_date=date(2025, 1, 2))
        assert subA.get_position("AAPL") is not None
        assert subB.get_position("AAPL") is None

    def test_settled_quantity_per_sub(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.update_position("600519", quantity=1000, price=50.0, cost=50000.0, trade_date=date(2025, 1, 2))
        assert sub.settled_quantity("600519", date(2025, 1, 2)) == 0
        assert sub.settled_quantity("600519", date(2025, 1, 3)) == 1000

    def test_nav_aggregation(self):
        master = Portfolio(initial_cash=100000)
        subA = SubPortfolio("A", 60000, master)
        subB = SubPortfolio("B", 40000, master)
        subA.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        subA.cash -= 15000
        subB.update_position("MSFT", quantity=50, price=200.0, cost=10000.0)
        subB.cash -= 10000
        total_nav = master.cash + subA.nav + subB.nav
        assert total_nav == pytest.approx(100000.0, rel=1e-4)

    def test_check_daily_loss(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.cash -= 5000
        assert sub.check_daily_loss(0.10) is True
        assert sub.check_daily_loss(0.20) is False

    def test_reset_daily(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.cash -= 5000
        sub.reset_daily()
        assert sub.starting_nav == sub.nav

    def test_close_returns_cash_to_master(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, trade_date=date(2025, 1, 3))
        sub.cash -= 15000
        sub.close_position("AAPL", 200.0)
        remaining = sub.close()
        assert remaining == pytest.approx(45000)
        assert sub.cash == 0
        assert sub.allocated_capital == 0
        assert len(sub.positions) == 0
        assert master.cash == pytest.approx(105000)

    def test_close_idempotent(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        assert sub.close() == 40000
        assert sub.close() == 0.0
        assert sub.close() == 0.0

    def test_close_after_profit_returns_all(self):
        master = Portfolio(initial_cash=100000)
        sub = SubPortfolio("test", 40000, master)
        sub.cash -= 30000
        sub.update_position("AAPL", quantity=200, price=150.0, cost=30000.0, trade_date=date(2025, 1, 2))
        sub.cash -= 30000
        sub.close_position("AAPL", 200.0)
        rem = sub.close()
        assert rem == 40000
        assert master.cash == 100000


class TestSubPortfolioBacktest:

    def test_multi_strategy_isolation_with_allocations(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

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

        data = _make_multi_symbol_data(["AAPL", "MSFT"], START, 5)
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
            strategy_allocations={"StratA": 0.5, "StratB": 0.5},
        )
        assert sA._positions.get("AAPL", 0) == 100
        assert sA._positions.get("MSFT", 0) == 0
        assert sB._positions.get("AAPL", 0) == 0
        assert sB._positions.get("MSFT", 0) == 100

        a_trades = [t for t in result.trades if t.strategy_name == "StratA"]
        b_trades = [t for t in result.trades if t.strategy_name == "StratB"]
        assert any(t.symbol == "AAPL" for t in a_trades)
        assert any(t.symbol == "MSFT" for t in b_trades)

    def test_allocation_respects_capital_limit(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class GreedyStrat:
            name = "Greedy"
            context = None
            _ordered = False

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if not self._ordered:
                    ctx.order_manager.submit_order("AAPL", 10000, "BUY", "MARKET", 100.0, "Greedy")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                pass

            def on_stop(self, ctx):
                pass

        data = _make_multi_symbol_data(["AAPL"], START, 5)
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[GreedyStrat()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
            strategy_allocations={"Greedy": 0.3},
        )
        greedy_trades = [t for t in result.trades if t.strategy_name == "Greedy"]
        if greedy_trades:
            assert greedy_trades[0].quantity * 100.0 <= 30000 + 1

    def test_unknown_strategy_allocation_rejected(self):
        bt = make_backtester({
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        })
        strategy = type("KnownStrategy", (), {"name": "Known"})()
        with pytest.raises(ValueError, match="unknown strategies"):
            bt.run(
                start=START, end=START,
                strategies=[strategy], initial_cash=100000,
                data_provider=None, symbols=["AAPL"],
                strategy_allocations={"Known": 0.5, "Ghost": 0.1},
            )

    def test_missing_strategy_allocation_rejected(self):
        bt = make_backtester({
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        })
        strat_a = type("StrategyA", (), {"name": "A"})()
        strat_b = type("StrategyB", (), {"name": "B"})()
        with pytest.raises(ValueError, match="missing strategies"):
            bt.run(
                start=START, end=START,
                strategies=[strat_a, strat_b], initial_cash=100000,
                data_provider=None, symbols=["AAPL"],
                strategy_allocations={"A": 0.5},
            )

    def test_duplicate_strategy_names_rejected_before_allocation(self):
        bt = make_backtester({
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        })
        strat_a = type("StrategyA", (), {"name": "Dup"})()
        strat_b = type("StrategyB", (), {"name": "Dup"})()
        with pytest.raises(ValueError, match="Duplicate strategy names"):
            bt.run(
                start=START, end=START,
                strategies=[strat_a, strat_b], initial_cash=100000,
                data_provider=None, symbols=["AAPL"],
            )

    def test_same_symbol_different_strategies(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuyerA:
            name = "BuyerA"
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BuyerA")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        class BuyerB:
            name = "BuyerB"
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "BuyerB")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = _make_multi_symbol_data(["AAPL"], START, 5)
        provider = DataFrameProvider(data)
        sA = BuyerA()
        sB = BuyerB()
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[sA, sB],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
            strategy_allocations={"BuyerA": 0.5, "BuyerB": 0.5},
        )
        assert sA._positions.get("AAPL", 0) == 100
        assert sB._positions.get("AAPL", 0) == 100
        total_qty = sum(
            pos["quantity"] for pos in result.open_positions
            if pos["symbol"] == "AAPL"
        )
        assert total_qty == 200

    def test_no_allocations_backward_compatible(self):
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class SingleStrat:
            name = "Single"
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
                    ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", 100.0, "Single")
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = _make_multi_symbol_data(["AAPL"], START, 5)
        provider = DataFrameProvider(data)
        s = SingleStrat()
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[s],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        assert s._positions.get("AAPL", 0) == 100
        assert len(result.trades) >= 1


def _make_multi_symbol_data(symbols, start, n_days):
    rows = []
    prices = {"AAPL": 100.0, "MSFT": 100.0}
    for i in range(n_days):
        ts = start + timedelta(days=i)
        for sym in symbols:
            p = prices.get(sym, 100.0)
            rows.append({
                "symbol": sym,
                "timestamp": ts,
                "open": p,
                "high": p + 1,
                "low": p - 1,
                "close": p,
                "volume": 1000000,
            })
    return pd.DataFrame(rows)
