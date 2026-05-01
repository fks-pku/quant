"""Type shapes for backtest engine internal data — zero runtime overhead, IDE auto-complete only.

total=False means all fields are optional, matching the Dict-based data flow.
"""

from __future__ import annotations

from datetime import datetime
from typing import NotRequired, TypedDict


class BacktestBar(TypedDict, total=False):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    _suspended: NotRequired[bool]


class DeferredOrder(TypedDict, total=False):
    symbol: str
    quantity: float
    side: str
    order_type: str
    price: float
    strategy: str
    _risk_check_price: NotRequired[float]
    _signal_date: NotRequired[datetime]
