"""Unit tests for core module."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from quant.core.events import EventBus, Event, EventType
from quant.core.portfolio import Portfolio, Position
from quant.core.scheduler import Scheduler


class TestEventBus:
    """Tests for EventBus."""

    def test_subscribe_and_publish(self):
        """Test basic subscribe and publish."""
        bus = EventBus()
        received = []

        def callback(event):
            received.append(event)

        bus.subscribe(EventType.BAR, callback)

        event = Event(EventType.BAR, datetime.now(), {"test": "data"})
        bus.publish(event)

        assert len(received) == 1
        assert received[0].data == {"test": "data"}

    def test_unsubscribe(self):
        """Test unsubscribe."""
        bus = EventBus()
        received = []

        def callback(event):
            received.append(event)

        bus.subscribe(EventType.BAR, callback)
        bus.unsubscribe(EventType.BAR, callback)

        event = Event(EventType.BAR, datetime.now(), {"test": "data"})
        bus.publish(event)

        assert len(received) == 0

    def test_publish_nowait(self):
        """Test fire-and-forget publish."""
        bus = EventBus()
        received = []

        def callback(event):
            received.append(event)

        bus.subscribe(EventType.QUOTE, callback)
        bus.publish_nowait(EventType.QUOTE, {"price": 100})

        assert len(received) == 1


class TestPortfolio:
    """Tests for Portfolio tracker."""

    def test_initialization(self):
        """Test portfolio initialization."""
        portfolio = Portfolio(initial_cash=50000, currency="USD")

        assert portfolio.cash == 50000
        assert portfolio.initial_cash == 50000
        assert portfolio.currency == "USD"
        assert portfolio.nav == 50000

    def test_update_position_buy(self):
        """Test updating position with buy."""
        portfolio = Portfolio(initial_cash=100000)

        portfolio.update_position("AAPL", quantity=10, price=150, cost=1500)

        assert "AAPL" in portfolio.positions
        assert portfolio.positions["AAPL"].quantity == 10
        assert portfolio.positions["AAPL"].avg_cost == 150

    def test_update_position_sell(self):
        """Test updating position with sell."""
        portfolio = Portfolio(initial_cash=100000)

        portfolio.update_position("AAPL", quantity=10, price=150, cost=1500)
        portfolio.update_position("AAPL", quantity=-5, price=155, cost=0)

        assert portfolio.positions["AAPL"].quantity == 5

    def test_close_position(self):
        """Test closing a position."""
        portfolio = Portfolio(initial_cash=100000)

        portfolio.update_position("AAPL", quantity=10, price=150, cost=1500)
        realized = portfolio.close_position("AAPL", price=160)

        assert realized == 100
        assert portfolio.positions["AAPL"].quantity == 0

    def test_nav_calculation(self):
        """Test NAV calculation."""
        portfolio = Portfolio(initial_cash=100000)

        portfolio.update_position("AAPL", quantity=10, price=150, cost=1500)

        expected_nav = portfolio.cash + 10 * 150
        assert portfolio.nav == expected_nav

    def test_sector_exposure(self):
        """Test sector exposure calculation."""
        portfolio = Portfolio(initial_cash=100000)

        portfolio.update_position("AAPL", quantity=10, price=150, cost=1500, sector="Technology")
        portfolio.update_position("GOOGL", quantity=5, price=100, cost=500, sector="Technology")

        exposure = portfolio.get_sector_exposure()

        assert "Technology" in exposure


class TestScheduler:
    """Tests for Scheduler."""

    def test_add_job(self):
        """Test adding a job to scheduler."""
        config = {"markets": {"US": {"timezone": "America/New_York", "open_hour": 9, "open_minute": 30, "close_hour": 16, "close_minute": 0}}}
        bus = EventBus()
        scheduler = Scheduler(config, bus)

        called = []

        def test_job():
            called.append(1)

        scheduler.add_job("test_job", "intraday", test_job, interval_minutes=5)

        assert len(scheduler.jobs) == 1
        assert scheduler.jobs[0].name == "test_job"

    def test_start_stop(self):
        """Test scheduler start and stop."""
        config = {"markets": {"US": {"timezone": "America/New_York", "open_hour": 9, "open_minute": 30, "close_hour": 16, "close_minute": 0}}}
        bus = EventBus()
        scheduler = Scheduler(config, bus)

        def test_job():
            pass

        scheduler.add_job("test_job", "intraday", test_job, interval_minutes=5)

        scheduler.start()
        assert scheduler._running is True

        scheduler.stop()
        assert scheduler._running is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
