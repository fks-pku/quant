"""Paper trading broker adapter with simulated execution using local market data."""

from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Any
import threading

from quant.domain.models.order import Order, OrderSide, OrderStatus, OrderType
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.exceptions import OrderRejectedError
from quant.runtime.execution_commission import total_commission
from quant.runtime.execution_simulator import (
    DEFAULT_RISK_PRICE_DEVIATION_LIMIT,
    RuntimeOrder,
    simulate_order_execution,
)
from quant.shared.utils.logger import setup_logger


class PaperBroker(BrokerAdapter):
    """Simulated broker for paper trading using real market data."""

    def __init__(
        self,
        initial_cash: float = 10000.0,
        slippage_bps: float = 5,
        data_provider: Any = None,
        commission_config: Any = None,
        execution_cost_model: Optional[Dict[str, Any]] = None,
        risk_price_deviation_limit: float = DEFAULT_RISK_PRICE_DEVIATION_LIMIT,
        market_impact_factor: float = 0.0,
        lot_sizes: Optional[Dict[str, int]] = None,
        ipo_dates: Optional[Dict[str, date]] = None,
    ):
        super().__init__("paper")
        self.initial_cash = initial_cash
        self.slippage_bps = slippage_bps
        self.data_provider = data_provider
        self.commission_config = commission_config or {}
        self.execution_cost_model = execution_cost_model
        self.risk_price_deviation_limit = risk_price_deviation_limit
        self.market_impact_factor = market_impact_factor
        self.lot_sizes = lot_sizes or {}
        self.ipo_dates = ipo_dates or {}
        self._portfolio_ref: Any = None
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self._lock = threading.RLock()
        self._next_order_id = 1
        self._latest_prices: Dict[str, float] = {}
        self._execution_bars: Dict[str, Dict[str, Any]] = {}
        self._previous_execution_bars: Dict[str, Dict[str, Any]] = {}
        self._trade_callbacks: List[Callable[..., None]] = []
        self._pending_trade_notifications: List[Dict[str, Any]] = []
        self.logger = setup_logger("PaperBroker")

    def set_portfolio(self, portfolio: Any) -> None:
        self._portfolio_ref = portfolio

    @property
    def cash(self) -> float:
        port = self._portfolio_ref
        if port is not None:
            return float(getattr(port, "cash", self.initial_cash) or 0.0)
        return self.initial_cash

    @property
    def positions(self) -> Dict[str, Any]:
        port = self._portfolio_ref
        if port is not None and hasattr(port, "positions"):
            return port.positions
        return {}

    def update_price(self, symbol: str, price: float) -> None:
        self._latest_prices[symbol] = price

    def set_execution_bars(self, bars: Any, trading_date: Optional[Any] = None) -> None:
        with self._lock:
            self._execution_bars = {}
            for bar in self._records(bars):
                symbol = self._bar_symbol(bar)
                if not symbol:
                    continue
                normalized = self._normalize_execution_bar(bar, symbol, trading_date)
                if normalized:
                    self._execution_bars[symbol] = normalized

    def set_previous_execution_bars(self, bars: Any, trading_date: Optional[Any] = None) -> None:
        with self._lock:
            self._previous_execution_bars = {}
            for bar in self._records(bars):
                symbol = self._bar_symbol(bar)
                if not symbol:
                    continue
                normalized = self._normalize_execution_bar(bar, symbol, trading_date)
                if normalized:
                    self._previous_execution_bars[symbol] = normalized

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
        side_value = order.side.value if isinstance(order.side, OrderSide) else order.side
        order_type = order.order_type.value if isinstance(order.order_type, OrderType) else str(order.order_type)
        runtime_order = RuntimeOrder(
            symbol=order.symbol,
            quantity=order.quantity,
            side=str(side_value).upper(),
            order_type=str(order_type).upper(),
            price=order.price,
            strategy=order.strategy_name,
            signal_date=order.timestamp,
        )
        try:
            simulation = simulate_order_execution(
                runtime_order,
                self._portfolio_ref,
                order.symbol,
                self._execution_bar_for_order(order.symbol),
                lot_sizes=self.lot_sizes,
                ipo_dates=self.ipo_dates,
                slippage_bps=self.slippage_bps,
                commission_config=self.commission_config,
                prev_bar=self._previous_execution_bars.get(order.symbol),
                risk_price_deviation_limit=self.risk_price_deviation_limit,
                market_impact_factor=self.market_impact_factor,
                execution_cost_model=self.execution_cost_model,
            )
        except OrderRejectedError as exc:
            self.logger.warning(f"Paper order rejected: {exc}")
            return self._set_order_attrs(order, {'status': OrderStatus.REJECTED})

        status = OrderStatus.FILLED
        if simulation.quantity + 1e-9 < order.quantity:
            status = OrderStatus.PARTIAL
        filled = self._set_order_attrs(order, {
            'status': status,
            'filled_quantity': simulation.quantity,
            'avg_fill_price': simulation.fill_price,
        })

        self._queue_trade_notification(
            filled,
            simulation.fill_price,
            simulation.commission,
            fill_quantity=simulation.quantity,
            timestamp=simulation.fill_time,
        )
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

    def estimate_commission(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        trade_date: Optional[date] = None,
    ) -> float:
        try:
            return float(total_commission(symbol, price, quantity, str(side).upper(), self.commission_config, trade_date))
        except Exception:
            return 0.0

    def _queue_trade_notification(
        self,
        order: Order,
        fill_price: float,
        commission: float,
        fill_quantity: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        side_value = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
        self._pending_trade_notifications.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": side_value,
            "quantity": fill_quantity if fill_quantity is not None else order.quantity,
            "price": fill_price,
            "commission": commission,
            "timestamp": timestamp or order.timestamp or datetime.now(),
            "strategy_name": order.strategy_name,
        })

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            return self.orders.get(order_id)

    def _execution_bar_for_order(self, symbol: str) -> Dict[str, Any]:
        bar = self._execution_bars.get(symbol)
        if bar:
            return bar
        price = self._get_current_price(symbol)
        return {
            "symbol": symbol,
            "timestamp": datetime.now(),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "open_price": price,
            "last_price": price,
            "volume": 0.0,
        }

    def _normalize_execution_bar(
        self,
        bar: Any,
        symbol: str,
        trading_date: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        open_price = self._positive_float(self._bar_value(bar, "open", "open_price", "openPrice"))
        if open_price is None:
            return None
        close_price = self._positive_float(
            self._bar_value(bar, "close", "close_price", "closePrice", "last_price", "price")
        )
        close_price = close_price if close_price is not None else open_price
        normalized = self._dict_copy(bar)
        normalized["symbol"] = symbol
        normalized["open"] = open_price
        normalized["open_price"] = open_price
        normalized["close"] = close_price
        normalized["last_price"] = close_price
        for key, names in {
            "high": ("high", "high_price", "highPrice"),
            "low": ("low", "low_price", "lowPrice"),
            "volume": ("volume", "vol", "turnover_volume"),
            "turnover": ("turnover", "amount", "value"),
            "adv20_value": ("adv20_value", "adv_value", "avg_turnover_20"),
            "adv20_volume": ("adv20_volume", "adv_volume", "avg_volume_20"),
            "volatility20": ("volatility20", "volatility_20d", "daily_volatility"),
            "up_limit": ("up_limit", "limit_up"),
            "down_limit": ("down_limit", "limit_down"),
            "is_st": ("is_st",),
            "tradable": ("tradable",),
            "has_daily_bar": ("has_daily_bar",),
            "_has_daily_bar": ("_has_daily_bar",),
            "_suspended": ("_suspended",),
        }.items():
            value = self._bar_value(bar, *names)
            if value is not None:
                normalized[key] = value
        normalized.setdefault("high", max(open_price, close_price))
        normalized.setdefault("low", min(open_price, close_price))
        timestamp = self._bar_value(bar, "timestamp", "datetime", "date", "trade_date")
        normalized["timestamp"] = self._normalize_timestamp(timestamp, trading_date)
        return normalized

    def _dict_copy(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return dict(item)
        if hasattr(item, "to_dict"):
            try:
                value = item.to_dict()
                if isinstance(value, dict):
                    return dict(value)
            except Exception:
                pass
        return {}

    def _normalize_timestamp(self, value: Any, trading_date: Optional[Any]) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if value is not None:
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                pass
        if isinstance(trading_date, datetime):
            return trading_date
        if isinstance(trading_date, date):
            return datetime.combine(trading_date, datetime.min.time())
        if trading_date is not None:
            try:
                return datetime.fromisoformat(str(trading_date))
            except ValueError:
                pass
        return datetime.now()

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

    def _portfolio_positions(self) -> Dict[str, Any]:
        port = self._portfolio_ref
        if port is not None and hasattr(port, "positions"):
            return port.positions
        return {}

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
            port = self._portfolio_ref
            if port is not None and hasattr(port, "positions"):
                return [pos for pos in port.positions.values() if getattr(pos, "quantity", 0) > 0]
            return []

    def get_account_info(self) -> AccountInfo:
        with self._lock:
            port = self._portfolio_ref
            if port is not None:
                cash = float(getattr(port, "cash", self.initial_cash) or 0.0)
                total_value = cash + sum(
                    getattr(pos, "market_value", 0.0) or 0.0
                    for pos in (getattr(port, "positions", {}) or {}).values()
                )
                return AccountInfo(
                    account_id="PAPER_ACCOUNT",
                    cash=cash,
                    buying_power=cash,
                    equity=total_value,
                    margin_used=0,
                )
            return AccountInfo(
                account_id="PAPER_ACCOUNT",
                cash=self.initial_cash,
                buying_power=self.initial_cash,
                equity=self.initial_cash,
                margin_used=0,
            )

    def get_order_status(self, order_id: str) -> OrderStatus:
        with self._lock:
            if order_id in self.orders:
                return self.orders[order_id].status
            return OrderStatus.REJECTED
