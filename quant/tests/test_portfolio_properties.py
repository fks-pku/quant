"""Tests for Portfolio starting_nav property and reset_daily."""

from quant.core.portfolio import Portfolio


def test_starting_nav_returns_initial_cash_on_creation():
    p = Portfolio(initial_cash=250000.0)
    assert p.starting_nav == 250000.0


def test_starting_nav_default_initial_cash():
    p = Portfolio()
    assert p.starting_nav == 100000.0


def test_reset_daily_updates_starting_nav_to_current_nav():
    p = Portfolio(initial_cash=100000.0)
    p.update_position("AAPL", quantity=10, price=150.0, cost=1500.0)
    p.cash -= 1500.0
    assert p.starting_nav == 100000.0
    p.reset_daily()
    assert p.starting_nav == p.nav


def test_starting_nav_is_accessible_as_property():
    p = Portfolio(initial_cash=50000.0)
    assert isinstance(Portfolio.starting_nav, property)
    assert p.starting_nav == 50000.0


def test_reset_daily_twice_tracks_nav_correctly():
    p = Portfolio(initial_cash=100000.0)
    p.update_position("AAPL", quantity=10, price=100.0, cost=1000.0)
    p.cash -= 1000.0
    p.reset_daily()
    first_nav = p.starting_nav
    p.update_position("AAPL", quantity=5, price=120.0, cost=600.0)
    p.cash -= 600.0
    p.reset_daily()
    assert p.starting_nav != first_nav
    assert p.starting_nav == p.nav
