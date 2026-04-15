"""Canonical Trade model (completed round-trip)."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Trade:
    """A completed trade with entry and exit."""

    entry_time: datetime
    exit_time: datetime
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
