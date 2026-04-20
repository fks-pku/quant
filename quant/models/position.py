"""Canonical Position model."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    """Canonical position representation used across the entire system."""

    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    sector: Optional[str] = None
