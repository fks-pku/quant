"""Tests for backtester bug fixes: entry_price averaging, portfolio price updates, commission, slippage."""

import pytest
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import patch

from quant.features.backtest.engine import Backtester, BacktestDiagnostics
from quant.features.trading.portfolio import Portfolio
from quant.domain.models.trade import Trade


def _make_diag():
    return BacktestDiagnostics()


class TestMultiBuyAveragesEntryPrice:
    def test_multi_buy_averages_entry_price(self):
        bt = Backtester(config={"backtest": {"slippage_bps": 0}, "execution": {}})
        portfolio = Portfolio(initial_cash=100000)
        entry_times = {}
        entry_prices = {}
        diag = _make_diag()

        bar1 = {"open": 100, "close": 100, "timestamp": datetime(2024, 1, 1)}
        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "market", "price": 100},
            portfolio, "AAPL", bar1, entry_times, entry_prices, diag,
        )

        assert portfolio.positions["AAPL"].avg_cost == 100
        assert portfolio.positions["AAPL"].quantity == 10

        bar2 = {"open": 110, "close": 110, "timestamp": datetime(2024, 1, 2)}
        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "market", "price": 110},
            portfolio, "AAPL", bar2, entry_times, entry_prices, diag,
        )

        assert portfolio.positions["AAPL"].avg_cost == 105
        assert portfolio.positions["AAPL"].quantity == 20


class TestSellPnlUsesAvgCost:
    def test_sell_pnl_uses_avg_cost(self):
        bt = Backtester(config={"backtest": {"slippage_bps": 0}, "execution": {}})
        portfolio = Portfolio(initial_cash=100000)
        entry_times = {}
        entry_prices = {}
        diag = _make_diag()

        bar1 = {"open": 100, "close": 100, "timestamp": datetime(2024, 1, 1)}
        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "market", "price": 100},
            portfolio, "AAPL", bar1, entry_times, entry_prices, diag,
        )

        bar2 = {"open": 110, "close": 110, "timestamp": datetime(2024, 1, 2)}
        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "market", "price": 110},
            portfolio, "AAPL", bar2, entry_times, entry_prices, diag,
        )

        bar3 = {"open": 120, "close": 120, "timestamp": datetime(2024, 1, 3)}
        trade = bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "SELL", "order_type": "market", "price": 120},
            portfolio, "AAPL", bar3, entry_times, entry_prices, diag,
        )

        assert trade is not None
        assert trade.entry_price == 105
        sell_commission = sum(trade.cost_breakdown.values())
        expected_pnl = (120 - 105) * 10 - sell_commission
        assert trade.pnl == pytest.approx(expected_pnl)


class TestPortfolioPricesUpdatedOncePerDay:
    def test_portfolio_prices_updated_once_per_day(self):
        config = {"backtest": {"slippage_bps": 0}, "execution": {}}
        bt = Backtester(config=config)

        ts = datetime(2024, 1, 2)
        bars_aapl = pd.DataFrame({"close": [100]}, index=[ts])
        bars_googl = pd.DataFrame({"close": [200]}, index=[ts])

        class MockDataProvider:
            def get_bars(self, symbol, start, end, timeframe):
                if symbol == "AAPL":
                    return bars_aapl
                return bars_googl

        class NoOpStrategy:
            def __init__(self):
                self.context = None
            def on_start(self, ctx):
                pass
            def on_data(self, ctx, bar):
                pass
            def on_before_trading(self, ctx, date):
                pass
            def on_after_trading(self, ctx, date):
                pass

        with patch.object(bt, "_update_portfolio_prices", wraps=bt._update_portfolio_prices) as mock_update:
            bt.run(
                start=datetime(2024, 1, 2),
                end=datetime(2024, 1, 2),
                strategies=[NoOpStrategy()],
                initial_cash=100000,
                data_provider=MockDataProvider(),
                symbols=["AAPL", "GOOGL"],
            )
            assert mock_update.call_count == 1


class TestCommissionUsPerShareCorrect:
    def test_commission_us_per_share_formula(self):
        bt = Backtester(config={"execution": {}})
        result = bt._calculate_commission_breakdown(100.0, 100, "US", "BUY")
        assert result["commission"] == max(100 * 0.005, 1.0)

    def test_commission_us_per_share_above_min(self):
        bt = Backtester(config={"execution": {}})
        result = bt._calculate_commission_breakdown(100.0, 1000, "US", "BUY")
        assert result["commission"] == 1000 * 0.005

    def test_commission_us_per_share_below_min(self):
        bt = Backtester(config={"execution": {}})
        result = bt._calculate_commission_breakdown(100.0, 10, "US", "BUY")
        assert result["commission"] == 1.0


class TestSlippageDirectionCorrect:
    def test_slippage_direction_correct(self):
        bt = Backtester(config={"backtest": {"slippage_bps": 100}, "execution": {}})
        portfolio = Portfolio(initial_cash=100000)
        entry_times = {}
        entry_prices = {}
        diag = _make_diag()

        slippage = 100 * (100 / 10000)

        bar = {"open": 100, "close": 100, "timestamp": datetime(2024, 1, 1)}
        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "market", "price": 100},
            portfolio, "AAPL", bar, entry_times, entry_prices, diag,
        )

        buy_commission = max(10 * 0.005, 1.0)
        expected_cash_after_buy = 100000 - (100 + slippage) * 10 - buy_commission
        assert portfolio.cash == pytest.approx(expected_cash_after_buy)

        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "SELL", "order_type": "market", "price": 100},
            portfolio, "AAPL", bar, entry_times, entry_prices, diag,
        )

        sell_commission = max(10 * 0.005, 1.0)
        expected_cash_after_sell = expected_cash_after_buy + (100 - slippage) * 10 - sell_commission
        assert portfolio.cash == pytest.approx(expected_cash_after_sell)


class TestCashTrackingAfterBuyAndSell:
    def test_cash_tracking_after_buy_and_sell(self):
        bt = Backtester(config={"backtest": {"slippage_bps": 0}, "execution": {}})
        portfolio = Portfolio(initial_cash=100000)
        entry_times = {}
        entry_prices = {}
        diag = _make_diag()

        bar1 = {"open": 100, "close": 100, "timestamp": datetime(2024, 1, 1)}
        bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "BUY", "order_type": "market", "price": 100},
            portfolio, "AAPL", bar1, entry_times, entry_prices, diag,
        )

        buy_commission = max(10 * 0.005, 1.0)
        assert portfolio.cash == 100000 - 100 * 10 - buy_commission

        bar2 = {"open": 110, "close": 110, "timestamp": datetime(2024, 1, 2)}
        trade = bt._execute_order(
            {"symbol": "AAPL", "quantity": 10, "side": "SELL", "order_type": "market", "price": 110},
            portfolio, "AAPL", bar2, entry_times, entry_prices, diag,
        )

        sell_commission = max(10 * 0.005, 1.0)
        expected_cash = 100000 - 100 * 10 - buy_commission + 110 * 10 - sell_commission
        assert portfolio.cash == pytest.approx(expected_cash)
        assert trade is not None


class TestOpenPositionsExtracted:
    def test_open_positions_in_result(self):
        bt = Backtester(config={"backtest": {"slippage_bps": 0}, "execution": {}, "risk": {"max_position_pct": 1.0}})

        dates = [datetime(2024, 1, d) for d in range(1, 11)]
        prices = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        df = pd.DataFrame({
            "symbol": ["AAPL"] * 10,
            "timestamp": dates,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000000] * 10,
        })

        from quant.features.backtest.walkforward import DataFrameProvider
        dp = DataFrameProvider(df)

        class BuyAndHoldStrategy:
            def __init__(self):
                self.context = None

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, date):
                pass

            def on_data(self, ctx, bar):
                sym = bar.get("symbol", "AAPL")
                if sym not in ctx.portfolio.positions:
                    price = bar.get("open", bar.get("close", 100))
                    qty = int(ctx.portfolio.cash / price / 2)
                    if qty > 0:
                        ctx.order_manager.submit_order(sym, qty, "BUY", "market", price, "BuyAndHold")

            def on_after_trading(self, ctx, date):
                pass

        strat = BuyAndHoldStrategy()

        result = bt.run(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 10),
            strategies=[strat],
            initial_cash=100000,
            data_provider=dp,
            symbols=["AAPL"],
        )

        assert hasattr(result, 'open_positions')
        assert isinstance(result.open_positions, list)
        assert len(result.open_positions) >= 1
        for pos in result.open_positions:
            assert "symbol" in pos
            assert "quantity" in pos
            assert "entry_price" in pos
            assert "entry_time" in pos
            assert "current_price" in pos
            assert "unrealized_pnl" in pos
            assert "market_value" in pos
            assert pos["quantity"] > 0
