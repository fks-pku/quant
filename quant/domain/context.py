"""StrategyContext — the interface strategies use to interact with the system.

Defined in domain so strategies depend on domain, not on any feature module.
Features (trading, backtest) provide concrete Context instances.
"""

from dataclasses import dataclass
from typing import Any, Optional


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
