"""Paper trading broker adapter with simulated execution using real market data."""

from datetime import date, datetime
from typing import Dict, List, Optional, Any
import threading

from quant.domain.models.order import Order, OrderSide, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.ports.broker import BrokerAdapter
from quant.shared.utils.logger import setup_logger


def _is_cn_symbol(symbol: str) -> bool:
    return (
        symbol.isdigit()
        and len(symbol) == 6
        and symbol[0] in ("0", "3", "6", "8", "9")
    )


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
        self._latest_prices[symbol] = price

    def set_data_provider(self, provider: Any) -> None:
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

            updated = self._set_order_attrs(order, {
                'order_id': order_id,
                'status': OrderStatus.SUBMITTED,
                'timestamp': datetime.now(),
            })
            self.orders[order_id] = updated

            filled = self._simulate_fill(updated)
            self.orders[order_id] = filled

            self.order_history.append(filled)
            fill_price_str = f"{filled.avg_fill_price:.2f}" if filled.avg_fill_price is not None else "N/A"
            self.logger.info(
                f"Order submitted: {order_id} {filled.side.value} {filled.quantity} {filled.symbol} "
                f"@ {fill_price_str}"
            )

            return order_id

    def _set_order_attrs(self, order: Order, updates: dict) -> Order:
        if hasattr(order, '__dataclass_fields__') and getattr(order, '__dataclass_params__', None) and order.__dataclass_params__.frozen:
            from dataclasses import fields
            kwargs = {}
            for f in fields(order):
                if f.name in updates:
                    kwargs[f.name] = updates[f.name]
                else:
                    kwargs[f.name] = getattr(order, f.name)
            return Order(**kwargs)
        else:
            for k, v in updates.items():
                try:
                    object.__setattr__(order, k, v)
                except (AttributeError, TypeError):
                    pass
            return order

    def _simulate_fill(self, order: Order) -> Order:
        current_price = self._get_current_price(order.symbol)

        side_value = order.side.value if isinstance(order.side, OrderSide) else order.side

        if side_value == "SELL" and _is_cn_symbol(order.symbol):
            pos = self.positions.get(order.symbol)
            if pos and pos.quantity > 0:
                today = date.today()
                settled = pos.settled_quantity(today)
                if order.quantity > settled:
                    self.logger.warning(
                        f"CN T+1 rejected: sell {order.quantity} {order.symbol}, "
                        f"only {settled} settled (bought before today)"
                    )
                    return self._set_order_attrs(order, {
                        'status': OrderStatus.REJECTED,
                    })

        slippage = current_price * (self.slippage_bps / 10000)
        if side_value == "BUY":
            fill_price = current_price + slippage
        else:
            fill_price = current_price - slippage

        filled = self._set_order_attrs(order, {
            'status': OrderStatus.FILLED,
            'filled_quantity': order.quantity,
            'avg_fill_price': fill_price,
        })

        self._update_position(filled, fill_price)
        return filled

    def _get_current_price(self, symbol: str) -> float:
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
        side_value = order.side.value if isinstance(order.side, OrderSide) else order.side

        if side_value == "BUY":
            total_cost = pos.avg_cost * pos.quantity + fill_price * order.quantity
            pos.quantity += order.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
            pos.add_buy_lot(date.today(), order.quantity)
            self.cash -= fill_price * order.quantity
        else:
            sell_qty = min(order.quantity, pos.quantity)
            self.cash += fill_price * sell_qty
            pos.quantity -= sell_qty
            pos.remove_sell_lots(sell_qty)
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

            cancelled = self._set_order_attrs(order, {'status': OrderStatus.CANCELLED})
            self.orders[order_id] = cancelled
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
