"""Paper trading broker adapter with simulated execution."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional
import random
import threading
import uuid

from quant.execution.brokers.base import (
    BrokerAdapter,
    Order,
    OrderStatus,
    Position,
    AccountInfo,
)
from quant.utils.logger import setup_logger


class PaperBroker(BrokerAdapter):
    """Internal simulated broker for paper trading."""

    def __init__(self, initial_cash: float = 100000.0, slippage_bps: float = 5):
        super().__init__("paper")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.slippage_bps = slippage_bps
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self._lock = threading.RLock()
        self._next_order_id = 1
        self.logger = setup_logger("PaperBroker")

    def connect(self) -> None:
        """Connect to paper broker (no-op)."""
        self._connected = True
        self.logger.info("Connected to Paper Broker")

    def disconnect(self) -> None:
        """Disconnect from paper broker (no-op)."""
        self._connected = False
        self.logger.info("Disconnected from Paper Broker")

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def submit_order(self, order: Order) -> str:
        """
        Submit an order to the paper broker.
        Simulates immediate fill at current price with slippage.
        """
        with self._lock:
            order_id = f"PAPER_{self._next_order_id}"
            self._next_order_id += 1

            order.order_id = order_id
            order.status = OrderStatus.SUBMITTED
            order.timestamp = datetime.now()
            self.orders[order_id] = order

            self._simulate_fill(order)

            self.order_history.append(order)
            self.logger.info(
                f"Order submitted: {order_id} {order.side} {order.quantity} {order.symbol} "
                f"@ {order.avg_fill_price:.2f}"
            )

            return order_id

    def _simulate_fill(self, order: Order) -> None:
        """Simulate order fill with slippage."""
        current_price = self._get_simulated_price(order.symbol)

        slippage = current_price * (self.slippage_bps / 10000)
        if order.side == "BUY":
            fill_price = current_price + slippage
        else:
            fill_price = current_price - slippage

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price

        self._update_position(order, fill_price)

    def _get_simulated_price(self, symbol: str) -> float:
        """Get a simulated price for a symbol (base price + random variation)."""
        base_prices = {
            "AAPL": 175.0,
            "GOOGL": 140.0,
            "MSFT": 380.0,
            "AMZN": 180.0,
            "TSLA": 250.0,
            "SPY": 470.0,
            "QQQ": 400.0,
        }
        base = base_prices.get(symbol, 100.0)
        variation = base * 0.001 * random.uniform(-1, 1)
        return base + variation

    def _update_position(self, order: Order, fill_price: float) -> None:
        """Update position after fill."""
        if order.symbol not in self.positions:
            self.positions[order.symbol] = Position(
                symbol=order.symbol,
                quantity=0,
                avg_cost=0,
                market_value=0,
                unrealized_pnl=0,
            )

        pos = self.positions[order.symbol]

        if order.side == "BUY":
            total_cost = pos.avg_cost * pos.quantity + fill_price * order.quantity
            pos.quantity += order.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
            self.cash -= fill_price * order.quantity
        else:
            pos.quantity -= order.quantity
            self.cash += fill_price * order.quantity
            if pos.quantity < 0:
                pos.quantity = 0
                pos.avg_cost = 0

        pos.market_value = pos.quantity * fill_price

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order (only if not yet filled)."""
        with self._lock:
            if order_id not in self.orders:
                return False

            order = self.orders[order_id]
            if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
                return False

            order.status = OrderStatus.CANCELLED
            self.logger.info(f"Order cancelled: {order_id}")
            return True

    def get_positions(self) -> List[Position]:
        """Get current positions."""
        with self._lock:
            return [pos for pos in self.positions.values() if pos.quantity > 0]

    def get_account_info(self) -> AccountInfo:
        """Get account information."""
        with self._lock:
            total_value = self.cash + sum(
                pos.market_value for pos in self.positions.values()
            )
            return AccountInfo(
                account_id="PAPER_ACCOUNT",
                cash=self.cash,
                buying_power=self.cash,
                equity=total_value,
                margin_used=0,
            )

    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get the status of an order."""
        with self._lock:
            if order_id in self.orders:
                return self.orders[order_id].status
            return OrderStatus.REJECTED
