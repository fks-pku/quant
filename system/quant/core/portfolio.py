"""Portfolio tracker for positions, NAV, and P&L."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import threading

import pandas as pd

from quant.models.position import Position


__all__ = ["Position", "PortfolioSnapshot", "Portfolio"]


@dataclass
class PortfolioSnapshot:
    """Snapshot of portfolio state at a point in time."""
    timestamp: datetime
    total_value: float
    cash: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl: float
    margin_used: float


class Portfolio:
    """Tracks positions, NAV, and P&L in-memory per session."""

    def __init__(self, initial_cash: float = 100000.0, currency: str = "USD"):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.currency = currency
        self.positions: Dict[str, Position] = {}
        self.orders: List[Dict[str, Any]] = []
        self.snapshots: List[PortfolioSnapshot] = []
        self._lock = threading.RLock()
        self._starting_nav = initial_cash
        self._daily_pnl = 0.0
        self._session_start = datetime.now()

    @property
    def nav(self) -> float:
        """Net Asset Value."""
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def total_unrealized_pnl(self) -> float:
        """Total unrealized P&L."""
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def total_realized_pnl(self) -> float:
        """Total realized P&L."""
        return sum(p.realized_pnl for p in self.positions.values())

    @property
    def margin_used(self) -> float:
        """Total margin used."""
        return sum(
            p.market_value * 0.5 for p in self.positions.values()
        )

    def update_position(
        self,
        symbol: str,
        quantity: float,
        price: float,
        cost: float,
        sector: Optional[str] = None,
    ) -> None:
        """Update or create a position."""
        with self._lock:
            if symbol not in self.positions:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=0,
                    avg_cost=0,
                    market_value=0,
                    unrealized_pnl=0,
                    realized_pnl=0,
                    sector=sector,
                )

            pos = self.positions[symbol]
            old_cost = pos.avg_cost * pos.quantity

            if quantity != 0:
                new_cost = cost + old_cost
                new_qty = quantity + pos.quantity
                pos.avg_cost = new_cost / new_qty if new_qty != 0 else 0
                pos.quantity = new_qty

            pos.market_value = pos.quantity * price
            pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

            if sector:
                pos.sector = sector

    def close_position(self, symbol: str, price: float) -> float:
        """Close a position and return realized P&L."""
        with self._lock:
            if symbol not in self.positions:
                return 0.0

            pos = self.positions[symbol]
            proceeds = pos.quantity * price
            cost_basis = pos.avg_cost * pos.quantity
            realized = proceeds - cost_basis

            self.cash += proceeds
            pos.realized_pnl += realized
            pos.quantity = 0
            pos.market_value = 0
            pos.unrealized_pnl = 0

            return realized

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get a position by symbol."""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Get all current positions."""
        with self._lock:
            return [
                pos for pos in self.positions.values() if pos.quantity != 0
            ]

    def get_sector_exposure(self) -> Dict[str, float]:
        """Get exposure by sector as percentage of NAV."""
        sector_values: Dict[str, float] = {}
        for pos in self.positions.values():
            if pos.sector and pos.quantity != 0:
                sector_values[pos.sector] = (
                    sector_values.get(pos.sector, 0) + pos.market_value
                )

        nav = self.nav if self.nav != 0 else 1
        return {k: v / nav for k, v in sector_values.items()}

    def check_daily_loss(self, limit_pct: float) -> bool:
        """Check if daily loss exceeds limit."""
        current_nav = self.nav
        loss = self._starting_nav - current_nav
        loss_pct = loss / self._starting_nav if self._starting_nav != 0 else 0
        return loss_pct > limit_pct

    def record_snapshot(self) -> None:
        """Record a portfolio snapshot."""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=self.nav,
            cash=self.cash,
            positions_value=sum(p.market_value for p in self.positions.values()),
            unrealized_pnl=self.total_unrealized_pnl,
            realized_pnl=self.total_realized_pnl,
            margin_used=self.margin_used,
        )
        self.snapshots.append(snapshot)

    def reset_daily(self) -> None:
        """Reset for a new trading day."""
        self._starting_nav = self.nav
        self._daily_pnl = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert portfolio to dictionary."""
        return {
            "nav": self.nav,
            "cash": self.cash,
            "currency": self.currency,
            "initial_cash": self.initial_cash,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "total_realized_pnl": self.total_realized_pnl,
            "margin_used": self.margin_used,
            "positions": {
                symbol: {
                    "quantity": pos.quantity,
                    "avg_cost": pos.avg_cost,
                    "market_value": pos.market_value,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "realized_pnl": pos.realized_pnl,
                    "sector": pos.sector,
                }
                for symbol, pos in self.positions.items()
                if pos.quantity != 0
            },
        }
