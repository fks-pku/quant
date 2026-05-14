"""Type shapes for backtest engine internal data — zero runtime overhead, IDE auto-complete only.

DeferredOrder is a frozen dataclass (was TypedDict). All fields required except risk_check_price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TypedDict

from typing_extensions import NotRequired

from quant.features.backtest.exceptions import OrderRejectedError, OrderRejectionReason


@dataclass(frozen=True)
class DeferredOrder:
    symbol: str
    quantity: float
    side: str
    order_type: str
    price: Optional[float]
    strategy: str
    signal_date: datetime
    risk_check_price: float = 0.0

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise OrderRejectedError(OrderRejectionReason.UNKNOWN_SIDE, self.symbol,
                                     f"side must be BUY or SELL, got {self.side!r}")
        if not isinstance(self.quantity, (int, float)) or self.quantity <= 0:
            raise OrderRejectedError(OrderRejectionReason.INVALID_QUANTITY, self.symbol,
                                     f"quantity must be > 0, got {self.quantity!r}")


class BacktestBar(TypedDict, total=False):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    _suspended: NotRequired[bool]
    tradable: NotRequired[bool]
    is_st: NotRequired[bool]
    st_type: NotRequired[str]
    up_limit: NotRequired[float]
    down_limit: NotRequired[float]
    has_daily_bar: NotRequired[bool]
    _has_daily_bar: NotRequired[bool]
