"""Paper trading broker adapter with simulated execution using local market data."""

from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Any
import threading

from quant.domain.models.order import Order, OrderSide, OrderStatus, OrderType
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.ports.broker import BrokerAdapter
from quant.runtime.execution_commission import total_commission
from quant.shared.utils.logger import setup_logger
from quant.shared.utils.symbol_utils import is_cn_symbol as _is_cn_symbol


class PaperBroker(BrokerAdapter):
    """Simulated broker for paper trading using real market data."""

    def __init__(
        self,
        initial_cash: float = 10000.0,
        slippage_bps: float = 5,
        data_provider: Any = None,
        commission_config: Any = None,
    ):
        super().__init__("paper")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.slippage_bps = slippage_bps
        self.data_provider = data_provider
        self.commission_config = commission_config or {}
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self._lock = threading.RLock()
        self._next_order_id = 1
        self._latest_prices: Dict[str, float] = {}
        self._execution_bars: Dict[str, Dict[str, float]] = {}
        self._trade_callbacks: List[Callable[..., None]] = []
        self._pending_trade_notifications: List[Dict[str, Any]] = []
        self.logger = setup_logger("PaperBroker")

    def update_price(self, symbol: str, price: float) -> None:
        self._latest_prices[symbol] = price

    def set_execution_bars(self, bars: Any, trading_date: Optional[Any] = None) -> None:
        with self._lock:
            self._execution_bars = {}
            for bar in self._records(bars):
                symbol = self._bar_symbol(bar)
                if not symbol:
                    continue
                open_price = self._positive_float(self._bar_value(bar, "open", "open_price", "openPrice"))
                close_price = self._positive_float(self._bar_value(bar, "close", "close_price", "closePrice"))
                if open_price is not None:
                    self._execution_bars[symbol] = {
                        "open_price": open_price,
                        "last_price": close_price if close_price is not None else open_price,
                    }

    def get_execution_reference_price(self, symbol: str, side: Optional[str] = None) -> Optional[Dict[str, float]]:
        bar = self._execution_bars.get(symbol)
        if bar and bar.get("open_price", 0.0) > 0:
            return dict(bar)
        price = self._get_current_price(symbol)
        if price <= 0:
            return None
        return {"last_price": price}

    def register_trade_callback(self, callback: Callable[..., None]) -> None:
        with self._lock:
            self._trade_callbacks.append(callback)

    def flush_trade_callbacks(self) -> None:
        with self._lock:
            notifications = list(self._pending_trade_notifications)
            self._pending_trade_notifications.clear()
            callbacks = list(self._trade_callbacks)
        for trade in notifications:
            for callback in callbacks:
                try:
                    callback(**trade)
                except Exception as e:
                    self.logger.error(f"Paper trade callback failed: {e}")

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
        order_type = order.order_type.value if isinstance(order.order_type, OrderType) else str(order.order_type)

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

        if order_type == "LIMIT":
            limit_price = self._positive_float(order.price)
            if limit_price is None:
                return self._set_order_attrs(order, {'status': OrderStatus.REJECTED})
            if side_value == "BUY" and current_price > limit_price:
                return self._set_order_attrs(order, {'status': OrderStatus.REJECTED})
            if side_value == "SELL" and current_price < limit_price:
                return self._set_order_attrs(order, {'status': OrderStatus.REJECTED})
            fill_price = current_price
        else:
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

        commission = self.estimate_commission(order.symbol, side_value, order.quantity, fill_price)
        self._update_position(filled, fill_price, commission)
        self._queue_trade_notification(filled, fill_price, commission)
        return filled

    def _get_current_price(self, symbol: str) -> float:
        bar = self._execution_bars.get(symbol)
        if bar and bar.get("open_price", 0.0) > 0:
            return float(bar["open_price"])

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

    def estimate_commission(self, symbol: str, side: str, quantity: float, price: float) -> float:
        try:
            return float(total_commission(symbol, price, quantity, str(side).upper(), self.commission_config))
        except Exception:
            return 0.0

    def _queue_trade_notification(self, order: Order, fill_price: float, commission: float) -> None:
        side_value = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
        self._pending_trade_notifications.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": side_value,
            "quantity": order.quantity,
            "price": fill_price,
            "commission": commission,
            "timestamp": order.timestamp or datetime.now(),
            "strategy_name": order.strategy_name,
        })

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            return self.orders.get(order_id)

    def _records(self, data: Any) -> List[Any]:
        if data is None:
            return []
        if isinstance(data, list):
            records: List[Any] = []
            for item in data:
                records.extend(self._records(item))
            return records
        if getattr(data, "empty", False):
            return []
        if hasattr(data, "to_dict"):
            return data.to_dict("records")
        if isinstance(data, (tuple, set)):
            return list(data)
        return [data]

    def _bar_symbol(self, bar: Any) -> Optional[str]:
        value = self._bar_value(bar, "symbol", "ts_code", "code", "ticker")
        return str(value) if value is not None and str(value) else None

    def _bar_value(self, item: Any, *names: str) -> Any:
        for name in names:
            if isinstance(item, dict) and name in item:
                return item[name]
            if hasattr(item, "get"):
                try:
                    value = item.get(name, None)
                    if value is not None:
                        return value
                except Exception:
                    pass
            if hasattr(item, name):
                value = getattr(item, name)
                if value is not None:
                    return value
        return None

    def _positive_float(self, value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number > 0 and number == number:
            return number
        return None

    def _update_position(self, order: Order, fill_price: float, commission: float) -> None:
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
            total_cost = pos.avg_cost * pos.quantity + fill_price * order.quantity + commission
            pos.quantity += order.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
            pos.add_buy_lot(date.today(), order.quantity)
            self.cash -= fill_price * order.quantity + commission
        else:
            sell_qty = min(order.quantity, pos.quantity)
            self.cash += fill_price * sell_qty - commission
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
