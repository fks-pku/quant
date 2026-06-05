"""StrategyContext — the interface strategies use to interact with the system.

Defined in domain so strategies depend on domain, not on any feature module.
Features (trading, backtest) provide concrete Context instances.
"""

from dataclasses import dataclass
from typing import Any, Optional


class StrategyScopedOrderManager:
    def __init__(self, delegate: Any, strategy_name: Optional[str] = None):
        self._delegate = delegate
        self._strategy_name = str(strategy_name) if strategy_name else None

    @property
    def strategy_name(self) -> Optional[str]:
        return self._strategy_name

    def submit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        strategy_name: Optional[str] = None,
    ) -> Any:
        scoped_name = self._resolve_strategy_name(strategy_name)
        return self._delegate.submit_order(
            symbol,
            quantity,
            side,
            order_type,
            price,
            scoped_name,
        )

    def drain_rejection_count(self) -> int:
        drain = getattr(self._delegate, "drain_rejection_count", None)
        if not callable(drain):
            return 0
        return int(drain() or 0)

    def _resolve_strategy_name(self, strategy_name: Optional[str]) -> Optional[str]:
        if not self._strategy_name:
            return strategy_name
        if strategy_name and str(strategy_name) != self._strategy_name:
            raise ValueError(
                f"strategy context for {self._strategy_name} cannot submit for {strategy_name}"
            )
        return self._strategy_name


@dataclass
class StrategyContext:
    """Strategy-facing context providing access to system components.

    Trading Engine and Backtester both create instances of this class.
    Strategies receive it via on_start() and on_before_trading/on_data/on_after_trading.
    """
    portfolio: Any
    risk_engine: Any
    event_bus: Any
    order_manager: Any = None
    execution_manager: Any = None
    data_provider: Any = None
    broker: Any = None
    execution_reference_resolver: Any = None
    signal_gate: Any = None
    strategy_name: Optional[str] = None
