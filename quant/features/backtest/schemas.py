"""Type shapes for backtest engine internal data — zero runtime overhead, IDE auto-complete only.

DeferredOrder is a frozen dataclass (was TypedDict). Cost-protection fields are optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TypedDict

from typing_extensions import NotRequired

from quant.features.backtest.exceptions import OrderRejectedError, OrderRejectionReason


EXECUTION_TIMING_NEXT_OPEN = "NEXT_OPEN"
EXECUTION_TIMING_SAME_CLOSE = "SAME_CLOSE"
VALID_EXECUTION_TIMINGS = {EXECUTION_TIMING_NEXT_OPEN, EXECUTION_TIMING_SAME_CLOSE}


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
    execution_timing: str = EXECUTION_TIMING_NEXT_OPEN
    execution_cost_reference_price: Optional[float] = None
    execution_cost_bps: Optional[float] = None
    execution_slippage_bps: Optional[float] = None
    execution_impact_bps: Optional[float] = None

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise OrderRejectedError(OrderRejectionReason.UNKNOWN_SIDE, self.symbol,
                                     f"side must be BUY or SELL, got {self.side!r}")
        if not isinstance(self.quantity, (int, float)) or self.quantity <= 0:
            raise OrderRejectedError(OrderRejectionReason.INVALID_QUANTITY, self.symbol,
                                     f"quantity must be > 0, got {self.quantity!r}")
        timing = str(self.execution_timing or EXECUTION_TIMING_NEXT_OPEN).upper()
        if timing not in VALID_EXECUTION_TIMINGS:
            raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, self.symbol,
                                     f"unsupported execution_timing={self.execution_timing!r}")
        object.__setattr__(self, "execution_timing", timing)


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
