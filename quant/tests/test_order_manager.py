"""Unit tests for OrderManager."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from quant.core.portfolio import Portfolio
from quant.core.risk import RiskEngine
from quant.core.events import EventBus, EventType
from quant.execution.order_manager import OrderManager
from quant.execution.brokers.base import Order, OrderStatus
from quant.execution.brokers.paper import PaperBroker


def _make_order_manager(config=None):
    config = config or {
        "risk": {
            "max_position_pct": 1.0,
            "max_sector_pct": 1.0,
            "max_daily_loss_pct": 1.0,
            "max_leverage": 10.0,
            "max_orders_minute": 100,
        }
    }
    portfolio = Portfolio(initial_cash=100000)
    bus = EventBus()
    risk = RiskEngine(config, portfolio, bus)
    om = OrderManager(portfolio, risk, bus, config)
    broker = PaperBroker(initial_cash=100000)
    om.register_broker("paper", broker)
    return om, portfolio, bus


class TestOrderManagerSubmit:
    def test_submit_order_approved(self):
        om, portfolio, bus = _make_order_manager()
        order_id = om.submit_order("AAPL", 10, "BUY", "MARKET", 150.0)
        assert order_id is not None
        order = om.get_order(order_id)
        assert order is not None
        assert order.symbol == "AAPL"
        assert order.quantity == 10

    def test_submit_order_rejected_by_risk(self):
        config = {
            "risk": {
                "max_position_pct": 0.01,
                "max_sector_pct": 0.01,
                "max_daily_loss_pct": 0.01,
                "max_leverage": 0.01,
                "max_orders_minute": 100,
            }
        }
        om, portfolio, bus = _make_order_manager(config)
        order_id = om.submit_order("AAPL", 10000, "BUY", "MARKET", 150.0)
        assert order_id is None


class TestOrderManagerCancel:
    def test_cancel_nonexistent_order(self):
        om, _, _ = _make_order_manager()
        result = om.cancel_order("nonexistent")
        assert result is False


class TestOrderManagerGetOrders:
    def test_get_all_orders(self):
        om, _, _ = _make_order_manager()
        om.submit_order("AAPL", 10, "BUY", "MARKET", 150.0)
        om.submit_order("MSFT", 5, "BUY", "MARKET", 300.0)
        orders = om.get_all_orders()
        assert len(orders) == 2

    def test_get_order_status(self):
        om, _, _ = _make_order_manager()
        order_id = om.submit_order("AAPL", 10, "BUY", "MARKET", 150.0)
        status = om.get_order_status(order_id)
        assert status is not None


class TestOrderManagerBrokerRouting:
    def test_default_broker_is_paper(self):
        om, _, _ = _make_order_manager()
        broker = om.get_broker_for_symbol("AAPL")
        assert isinstance(broker, PaperBroker)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
