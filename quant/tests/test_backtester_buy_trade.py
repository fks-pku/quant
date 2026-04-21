"""Tests that BUY orders create Trade objects with correct fields."""

from datetime import datetime
from unittest.mock import MagicMock

from quant.features.backtest.engine import Backtester
from quant.shared.models.trade import Trade


def _make_backtester():
    config = {"backtest": {"slippage_bps": 0}, "execution": {"commission": {}}}
    return Backtester(config=config)


def _make_bar(price=100.0, volume=100000):
    return {
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": volume,
        "timestamp": datetime(2024, 1, 10),
    }


def test_buy_returns_trade_not_none():
    bt = _make_backtester()
    from quant.features.trading.portfolio import Portfolio
    from quant.features.backtest.engine import BacktestDiagnostics

    portfolio = Portfolio(initial_cash=100000.0)
    entry_times = {}
    entry_prices = {}
    diag = BacktestDiagnostics()

    order = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "BUY",
        "order_type": "MARKET",
        "price": None,
        "strategy": "test",
        "_signal_date": datetime(2024, 1, 9),
        "_deferred_days": 0,
    }

    trade = bt._execute_order(order, portfolio, "AAPL", _make_bar(), entry_times, entry_prices, diag)
    assert trade is not None
    assert isinstance(trade, Trade)


def test_buy_trade_pnl_is_negative_commission():
    bt = _make_backtester()
    from quant.features.trading.portfolio import Portfolio
    from quant.features.backtest.engine import BacktestDiagnostics

    portfolio = Portfolio(initial_cash=100000.0)
    entry_times = {}
    entry_prices = {}
    diag = BacktestDiagnostics()

    order = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "BUY",
        "order_type": "MARKET",
        "price": None,
        "strategy": "test",
        "_signal_date": datetime(2024, 1, 9),
        "_deferred_days": 0,
    }

    trade = bt._execute_order(order, portfolio, "AAPL", _make_bar(100.0), entry_times, entry_prices, diag)
    assert trade is not None
    assert trade.pnl <= 0
    commission = sum(trade.cost_breakdown.values())
    assert trade.pnl == -commission


def test_buy_trade_entry_price_equals_fill_price():
    bt = _make_backtester()
    from quant.features.trading.portfolio import Portfolio
    from quant.features.backtest.engine import BacktestDiagnostics

    portfolio = Portfolio(initial_cash=100000.0)
    entry_times = {}
    entry_prices = {}
    diag = BacktestDiagnostics()

    order = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "BUY",
        "order_type": "MARKET",
        "price": None,
        "strategy": "test",
        "_signal_date": datetime(2024, 1, 9),
        "_deferred_days": 0,
    }

    trade = bt._execute_order(order, portfolio, "AAPL", _make_bar(100.0), entry_times, entry_prices, diag)
    assert trade is not None
    assert trade.entry_price == trade.fill_price


def test_buy_trade_side_is_buy():
    bt = _make_backtester()
    from quant.features.trading.portfolio import Portfolio
    from quant.features.backtest.engine import BacktestDiagnostics

    portfolio = Portfolio(initial_cash=100000.0)
    entry_times = {}
    entry_prices = {}
    diag = BacktestDiagnostics()

    order = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "BUY",
        "order_type": "MARKET",
        "price": None,
        "strategy": "test",
        "_signal_date": datetime(2024, 1, 9),
        "_deferred_days": 0,
    }

    trade = bt._execute_order(order, portfolio, "AAPL", _make_bar(), entry_times, entry_prices, diag)
    assert trade is not None
    assert trade.side == "BUY"


def test_buy_trade_updates_portfolio_cash():
    bt = _make_backtester()
    from quant.features.trading.portfolio import Portfolio
    from quant.features.backtest.engine import BacktestDiagnostics

    portfolio = Portfolio(initial_cash=100000.0)
    entry_times = {}
    entry_prices = {}
    diag = BacktestDiagnostics()

    order = {
        "symbol": "AAPL",
        "quantity": 10,
        "side": "BUY",
        "order_type": "MARKET",
        "price": None,
        "strategy": "test",
        "_signal_date": datetime(2024, 1, 9),
        "_deferred_days": 0,
    }

    initial_cash = portfolio.cash
    trade = bt._execute_order(order, portfolio, "AAPL", _make_bar(100.0), entry_times, entry_prices, diag)
    assert trade is not None
    assert portfolio.cash < initial_cash
