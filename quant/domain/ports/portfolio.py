"""Ports for portfolio and risk engine — contracts that backtest depends on."""

from datetime import date
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from quant.domain.models.risk_check import RiskCheckResult


@runtime_checkable
class PortfolioLike(Protocol):
    """Contract for portfolio implementations used by backtest and trading engines.

    Uses Protocol (structural typing) so any class with matching attributes/methods
    satisfies this contract without needing explicit ABC inheritance.
    """

    cash: float
    nav: float
    positions: Dict[str, Any]

    def get_position(self, symbol: str) -> Optional[Any]: ...
    def update_position(
        self, symbol: str, quantity: float, price: float, cost: float,
        trade_date: Optional[date] = None,
        realized_pnl: Optional[float] = None,
    ) -> None: ...
    def can_afford(self, cost: float) -> bool: ...
    def reset_daily(self) -> None: ...


@runtime_checkable
class RiskEngineLike(Protocol):
    """Contract for risk engine implementations used by backtest and trading engines."""

    def check_order(
        self, symbol: str, quantity: float, price: float, order_value: float,
        sector: Optional[str] = None, side: Optional[str] = None,
        as_of_date: Optional[date] = None,
    ) -> Tuple[bool, List[RiskCheckResult]]: ...
    def record_order(
        self, symbol: Optional[str] = None, order_value: float = 0.0,
        as_of_date: Optional[date] = None,
    ) -> None: ...
    def reset_daily(self) -> None: ...
