"""Unit tests for execution layer."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.infrastructure.events import EventBus, EventType
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus


class TestPaperBroker:
    """Tests for PaperBroker."""

    def test_initialization(self):
        """Test paper broker initialization."""
        broker = PaperBroker(initial_cash=100000, slippage_bps=5)

        assert broker.name == "paper"
        assert broker.cash == 100000
        assert broker.slippage_bps == 5

    def test_connect_disconnect(self):
        """Test connect and disconnect."""
        broker = PaperBroker()

        broker.connect()
        assert broker.is_connected() is True

        broker.disconnect()
        assert broker.is_connected() is False

    def test_submit_order(self):
        """Test submitting an order."""
        broker = PaperBroker(initial_cash=100000)

        order = Order(
            symbol="AAPL",
            quantity=10,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )

        order_id = broker.submit_order(order)

        assert order_id is not None
        assert order_id.startswith("PAPER_")
        assert broker.orders[order_id].status == OrderStatus.FILLED

    def test_cancel_order(self):
        """Test cancelling an order."""
        broker = PaperBroker(initial_cash=100000)

        order = Order(
            symbol="AAPL",
            quantity=10,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )

        order_id = broker.submit_order(order)
        success = broker.cancel_order(order_id)

        assert success is False

    def test_get_positions(self):
        """Test getting positions."""
        broker = PaperBroker(initial_cash=100000)

        order = Order(
            symbol="AAPL",
            quantity=10,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )

        broker.submit_order(order)
        positions = broker.get_positions()

        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"

    def test_get_account_info(self):
        """Test getting account info."""
        broker = PaperBroker(initial_cash=100000)

        account = broker.get_account_info()

        assert account.cash <= 100000
        assert account.buying_power == account.cash


class TestOrder:
    """Tests for Order dataclass."""

    def test_order_creation(self):
        """Test creating an order."""
        order = Order(
            symbol="AAPL",
            quantity=10,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )

        assert order.symbol == "AAPL"
        assert order.quantity == 10
        assert order.side == OrderSide.BUY
        assert order.status == OrderStatus.PENDING


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
