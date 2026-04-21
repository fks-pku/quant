"""Paper trading broker adapter with simulated execution using real market data."""

from datetime import datetime
from typing import Dict, List, Optional, Any
import threading

from quant.shared.models import Order, OrderStatus, Position, AccountInfo
from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.shared.utils.logger import setup_logger


class PaperBroker(BrokerAdapter):
    """Simulated broker for paper trading using real market data."""

    def __init__(
        self,
        initial_cash: float = 100000.0,
        slippage_bps: float = 5,
        data_provider: Any = None,
    ):
        super().__init__("paper")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.slippage_bps = slippage_bps
        self.data_provider = data_provider
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self._lock = threading.RLock()
        self._next_order_id = 1
        self._latest_prices: Dict[str, float] = {}
        self.logger = setup_logger("PaperBroker")

    def update_price(self, symbol: str, price: float) -> None:
        """Update the latest known price for a symbol (called by data feed)."""
        self._latest_prices[symbol] = price

    def set_data_provider(self, provider: Any) -> None:
        """Set or replace the data provider for price lookups."""
        self.data_provider = provider

    def connect(self) -> None:
        self._connected = True
        self.logger.info("Connected to Paper Broker")

    def disconnect(self) -> None:
        self._connected = False
        self.logger.info("Disconnected from Paper Broker")

    def is_connected(self) -> bool:
        return self._connected

    def submit_order(self, order: Order) -> str:
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
        current_price = self._get_current_price(order.symbol)

        slippage = current_price * (self.slippage_bps / 10000)
        if order.side == "BUY":
            fill_price = current_price + slippage
        else:
            fill_price = current_price - slippage

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price

        self._update_position(order, fill_price)

    def _get_current_price(self, symbol: str) -> float:
        """Get current price: prefer cached latest price, then data provider, then fallback."""
        if symbol in self._latest_prices:
            return self._latest_prices[symbol]

        if self.data_provider and hasattr(self.data_provider, "get_quote"):
            try:
                quote = self.data_provider.get_quote(symbol)
                if quote and hasattr(quote, "last_price") and quote.last_price > 0:
                    return quote.last_price
                if isinstance(quote, dict) and quote.get("last_price", 0) > 0:
                    return quote["last_price"]
            except Exception:
                pass

        if self.data_provider and hasattr(self.data_provider, "get_bars"):
            try:
                from datetime import timedelta
                end = datetime.now()
                start = end - timedelta(days=5)
                bars = self.data_provider.get_bars(symbol, start, end, "1d")
                if bars is not None and not bars.empty:
                    return float(bars["close"].iloc[-1])
            except Exception:
                pass

        self.logger.warning(f"No price data for {symbol}, using fallback 100.0")
        return 100.0

    def _update_position(self, order: Order, fill_price: float) -> None:
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
            sell_qty = min(order.quantity, pos.quantity)
            self.cash += fill_price * sell_qty
            pos.quantity -= sell_qty
            if pos.quantity <= 0:
                pos.quantity = 0
                pos.avg_cost = 0

        pos.market_value = pos.quantity * fill_price

    def cancel_order(self, order_id: str) -> bool:
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
        with self._lock:
            return [pos for pos in self.positions.values() if pos.quantity > 0]

    def get_account_info(self) -> AccountInfo:
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
        with self._lock:
            if order_id in self.orders:
                return self.orders[order_id].status
            return OrderStatus.REJECTED
