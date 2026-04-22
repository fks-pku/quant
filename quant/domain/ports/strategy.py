from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from quant.domain.models.bar import Bar
from quant.domain.models.order import Order, OrderSide, OrderType
from quant.domain.models.position import Position
from quant.domain.events.base import Event, EventType

EventSubscriber = Callable[[Event], None]


@runtime_checkable
class PortfolioAccessor(Protocol):
    def submit_order(self, order: Order) -> str: ...
    def get_position(self, symbol: str) -> Optional[Position]: ...
    def get_all_positions(self) -> Dict[str, Position]: ...


@runtime_checkable
class DataProvider(Protocol):
    def get_bars(self, symbol: str, lookback: int = 100) -> Any: ...


class StrategyContext:
    def __init__(
        self,
        strategy_name: str,
        event_publisher: "EventPublisher",
        data_provider: DataProvider,
        portfolio_accessor: PortfolioAccessor,
        symbols: List[str],
        parameters: Dict[str, Any],
    ):
        self.strategy_name = strategy_name
        self.event_publisher = event_publisher
        self.data_provider = data_provider
        self.portfolio_accessor = portfolio_accessor
        self.symbols = symbols
        self.parameters = parameters


class Strategy(ABC):

    def __init__(self, name: str):
        self._name = name
        self._context: Optional[StrategyContext] = None
        self._positions: Dict[str, Position] = {}
        self._data: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def context(self) -> Optional[StrategyContext]:
        return self._context

    @context.setter
    def context(self, ctx: StrategyContext) -> None:
        self._context = ctx

    @property
    def symbols(self) -> List[str]:
        if self._context:
            return self._context.symbols
        return []

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def on_before_trading(self) -> None:
        pass

    def on_after_trading(self) -> None:
        pass

    def on_fill(self, order: Order) -> None:
        pass

    def on_order_rejected(self, order: Order) -> None:
        pass

    @abstractmethod
    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        pass

    def buy(
        self,
        symbol: str,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> str:
        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=OrderSide.BUY,
            order_type=order_type,
            price=price,
            stop_price=stop_price,
            strategy_name=self._name,
        )
        return self._submit_order(order)

    def sell(
        self,
        symbol: str,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> str:
        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=OrderSide.SELL,
            order_type=order_type,
            price=price,
            stop_price=stop_price,
            strategy_name=self._name,
        )
        return self._submit_order(order)

    def get_position(self, symbol: str) -> Optional[Position]:
        if self._context and self._context.portfolio_accessor:
            return self._context.portfolio_accessor.get_position(symbol)
        return self._positions.get(symbol)

    def get_all_positions(self) -> Dict[str, Position]:
        if self._context and self._context.portfolio_accessor:
            return self._context.portfolio_accessor.get_all_positions()
        return dict(self._positions)

    def _submit_order(self, order: Order) -> str:
        if self._context and self._context.portfolio_accessor:
            return self._context.portfolio_accessor.submit_order(order)
        raise RuntimeError("Strategy has no context with portfolio accessor")

    def _load_data(self, key: str) -> Optional[Any]:
        return self._data.get(key)

    def _store_data(self, key: str, value: Any) -> None:
        self._data[key] = value

    def _get_data(self, symbol: str, lookback: int = 100) -> Any:
        if self._context and self._context.data_provider:
            return self._context.data_provider.get_bars(symbol, lookback)
        return None
