"""Canonical Trade model (completed round-trip)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


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
    signal_date: Optional[datetime] = None
    fill_date: Optional[datetime] = None
    fill_price: float = 0.0
    intended_qty: float = 0.0
    cost_breakdown: Optional[Dict] = None
