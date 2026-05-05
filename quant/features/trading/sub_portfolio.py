"""Virtual sub-account for strategy isolation within a shared capital pool.

SubPortfolio is a drop-in replacement for Portfolio (duck-typed). Each strategy
gets its own SubPortfolio with an allocated share of the master Portfolio's cash.
Positions are tracked independently per strategy.

Capital isolation model (fund-like):
  - On creation: allocated_capital is deducted from master's cash pool
  - During trading: sub manages its own cash — no cross-leakage to/from master
  - On close(): remaining cash returns to master, sub is decommissioned
  - SubPortfolio.cash = allocated_capital - _cash_used (computed, NOT mirrored)
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Any
import threading

from quant.domain.models.position import Position
from quant.domain.models.market import is_cn_symbol as _is_cn_symbol
from quant.domain.ports.portfolio import PortfolioLike


class SubPortfolio(PortfolioLike):
    """Virtual sub-account for a strategy within a shared capital pool."""

    def __init__(self, strategy_name: str, allocated_capital: float, master: Any):
        self.strategy_name = strategy_name
        self.allocated_capital = allocated_capital
        self._master = master
        self.positions: Dict[str, Position] = {}
        self._cash_used: float = 0.0
        self._lock = threading.RLock()
        self._starting_nav = allocated_capital
        self.initial_cash = allocated_capital
        self.currency = master.currency if hasattr(master, 'currency') else "USD"
        self.orders: List[Dict[str, Any]] = []
        self.snapshots: List[Any] = []
        self._closed: bool = False
        master.cash -= allocated_capital

    @property
    def cash(self) -> float:
        return self.allocated_capital - self._cash_used

    @cash.setter
    def cash(self, value: float):
        if value < 0:
            value = 0.0
        self._cash_used = self.allocated_capital - value

    @property
    def nav(self) -> float:
        with self._lock:
            return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def starting_nav(self) -> float:
        return self._starting_nav

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self.positions.values())

    @property
    def margin_used(self) -> float:
        return sum(p.market_value * 0.5 for p in self.positions.values())

    def can_afford(self, cost: float) -> bool:
        return self.cash >= cost

    def update_position(
        self,
        symbol: str,
        quantity: float,
        price: float,
        cost: float,
        sector: Optional[str] = None,
        trade_date: Optional[date] = None,
        realized_pnl: Optional[float] = None,
        lot_price: Optional[float] = None,
    ) -> None:
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

            if quantity != 0:
                if quantity > 0:
                    new_cost = cost + pos.avg_cost * pos.quantity
                    new_qty = quantity + pos.quantity
                    pos.avg_cost = new_cost / new_qty if new_qty != 0 else 0
                    pos.quantity = new_qty
                    if trade_date is not None:
                        effective_lot_price = lot_price if lot_price is not None else price
                        pos.add_buy_lot(trade_date, quantity, effective_lot_price)
                else:
                    pos.quantity += quantity
                    pos.remove_sell_lots(abs(quantity), fill_price=price)
                    if realized_pnl is not None:
                        pos.realized_pnl += realized_pnl
                    if abs(pos.quantity) < 1e-10:
                        pos.quantity = 0.0
                        pos.avg_cost = 0.0
                    else:
                        pos.recalc_avg_cost_from_lots()

            pos.market_value = pos.quantity * price
            pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

            if sector:
                pos.sector = sector

    def close_position(self, symbol: str, price: float) -> float:
        with self._lock:
            if symbol not in self.positions:
                return 0.0
            pos = self.positions[symbol]
            proceeds = pos.quantity * price
            cost_basis = pos.avg_cost * pos.quantity
            realized = proceeds - cost_basis
            old_qty = pos.quantity
            pos.realized_pnl += realized
            pos.quantity = 0
            pos.market_value = 0
            pos.unrealized_pnl = 0
            self.cash += proceeds
            return realized

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        with self._lock:
            return [pos for pos in self.positions.values() if pos.quantity != 0]

    @staticmethod
    def is_cn_symbol(symbol: str) -> bool:
        return _is_cn_symbol(symbol)

    def settled_quantity(self, symbol: str, as_of: date) -> float:
        with self._lock:
            pos = self.positions.get(symbol)
            if pos is None:
                return 0.0
            return pos.settled_quantity(as_of)

    def get_sector_exposure(self) -> Dict[str, float]:
        sector_values: Dict[str, float] = {}
        for pos in self.positions.values():
            if pos.sector and pos.quantity != 0:
                sector_values[pos.sector] = (
                    sector_values.get(pos.sector, 0) + pos.market_value
                )
        nav = self.nav if self.nav != 0 else 1
        return {k: v / nav for k, v in sector_values.items()}

    def check_daily_loss(self, limit_pct: float) -> bool:
        current_nav = self.nav
        loss = self._starting_nav - current_nav
        loss_pct = loss / self._starting_nav if self._starting_nav != 0 else 0
        return loss_pct > limit_pct

    def record_snapshot(self) -> None:
        pass

    def reset_daily(self) -> None:
        self._starting_nav = self.nav

    def close(self) -> float:
        """Return remaining capital to master and decommission this sub-account."""
        with self._lock:
            if self._closed:
                return 0.0
            remaining = self.cash
            if remaining > 0:
                self._master.cash += remaining
            self._cash_used = 0.0
            self.allocated_capital = 0.0
            self.positions.clear()
            self._closed = True
            return remaining

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "nav": self.nav,
            "cash": self.cash,
            "allocated_capital": self.allocated_capital,
            "currency": self.currency,
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
