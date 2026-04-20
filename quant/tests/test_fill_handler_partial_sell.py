"""Tests for FillHandler partial sell position tracking."""

from datetime import datetime

from quant.core.events import EventBus
from quant.core.portfolio import Portfolio
from quant.execution.fill_handler import FillHandler


def _make_handler(initial_cash=100000.0):
    portfolio = Portfolio(initial_cash=initial_cash)
    event_bus = EventBus()
    config = {}
    return FillHandler(portfolio, event_bus, config), portfolio


def test_partial_sell_leaves_remaining_position():
    handler, portfolio = _make_handler()
    handler.process_fill(
        order_id="o1", symbol="AAPL", side="BUY",
        quantity=100, price=50.0, commission=0.0,
        timestamp=datetime.now(),
    )
    handler.process_fill(
        order_id="o2", symbol="AAPL", side="SELL",
        quantity=30, price=55.0, commission=0.0,
        timestamp=datetime.now(),
    )
    pos = portfolio.get_position("AAPL")
    assert pos is not None
    assert pos.quantity == 70


def test_full_sell_closes_position():
    handler, portfolio = _make_handler()
    handler.process_fill(
        order_id="o1", symbol="AAPL", side="BUY",
        quantity=100, price=50.0, commission=0.0,
        timestamp=datetime.now(),
    )
    handler.process_fill(
        order_id="o2", symbol="AAPL", side="SELL",
        quantity=100, price=55.0, commission=0.0,
        timestamp=datetime.now(),
    )
    pos = portfolio.get_position("AAPL")
    assert pos is not None
    assert pos.quantity == 0


def test_buy_fill_updates_position_correctly():
    handler, portfolio = _make_handler()
    handler.process_fill(
        order_id="o1", symbol="AAPL", side="BUY",
        quantity=50, price=100.0, commission=0.0,
        timestamp=datetime.now(),
    )
    pos = portfolio.get_position("AAPL")
    assert pos is not None
    assert pos.quantity == 50
    assert pos.avg_cost == 100.0


def test_sell_without_position_does_nothing():
    handler, portfolio = _make_handler()
    initial_cash = portfolio.cash
    handler.process_fill(
        order_id="o1", symbol="AAPL", side="SELL",
        quantity=50, price=100.0, commission=0.0,
        timestamp=datetime.now(),
    )
    assert portfolio.get_position("AAPL") is None
    assert portfolio.cash == initial_cash


def test_sell_more_than_held_sells_max_available():
    handler, portfolio = _make_handler()
    handler.process_fill(
        order_id="o1", symbol="AAPL", side="BUY",
        quantity=50, price=100.0, commission=0.0,
        timestamp=datetime.now(),
    )
    handler.process_fill(
        order_id="o2", symbol="AAPL", side="SELL",
        quantity=200, price=110.0, commission=0.0,
        timestamp=datetime.now(),
    )
    pos = portfolio.get_position("AAPL")
    assert pos is not None
    assert pos.quantity == 0
