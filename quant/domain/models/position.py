from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, NamedTuple, Optional, Tuple


class LotEntry(NamedTuple):
    qty: float
    price: float


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    sector: Optional[str] = None
    _lots: Dict[date, LotEntry] = field(default_factory=dict, repr=False)

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return abs(self.quantity) < 1e-10

    @property
    def cost_basis(self) -> float:
        return abs(self.quantity) * self.avg_cost

    def settled_quantity(self, as_of: date) -> float:
        settled = 0.0
        for lot_date, lot in self._lots.items():
            if lot_date < as_of:
                settled += lot.qty
        return min(settled, self.quantity)

    def add_buy_lot(self, buy_date: date, qty: float, price: float = 0.0) -> None:
        existing = self._lots.get(buy_date)
        if existing:
            total_qty = existing.qty + qty
            avg_price = (existing.price * existing.qty + price * qty) / total_qty if total_qty > 0 else 0.0
            self._lots[buy_date] = LotEntry(total_qty, avg_price)
        else:
            self._lots[buy_date] = LotEntry(qty, price)

    def remove_sell_lots(self, sell_qty: float) -> List[Tuple[date, float, float]]:
        consumed: List[Tuple[date, float, float]] = []
        remaining = sell_qty
        sorted_dates = sorted(self._lots.keys())
        for d in sorted_dates:
            if remaining <= 0:
                break
            lot = self._lots[d]
            take = min(lot.qty, remaining)
            consumed.append((d, take, lot.price))
            new_qty = lot.qty - take
            if new_qty < 1e-10:
                del self._lots[d]
            else:
                self._lots[d] = LotEntry(new_qty, lot.price)
            remaining -= take
        if self.is_flat:
            self._lots.clear()
        return consumed

    def update_from_fill(self, fill_quantity: float, fill_price: float, fill_date: Optional[date] = None) -> None:
        if self.quantity == 0:
            self.quantity = fill_quantity
            self.avg_cost = fill_price
        elif (self.quantity > 0 and fill_quantity > 0) or (self.quantity < 0 and fill_quantity < 0):
            total_cost = self.avg_cost * abs(self.quantity) + fill_price * abs(fill_quantity)
            self.quantity += fill_quantity
            if abs(self.quantity) > 0:
                self.avg_cost = total_cost / abs(self.quantity)
        else:
            closing_qty = min(abs(self.quantity), abs(fill_quantity))
            pnl_per_share = (fill_price - self.avg_cost) if self.quantity > 0 else (self.avg_cost - fill_price)
            self.realized_pnl += pnl_per_share * closing_qty
            self.quantity += fill_quantity
            if abs(self.quantity) < 1e-10:
                self.quantity = 0.0
                self.avg_cost = 0.0
            elif self.quantity < 0 and fill_quantity < 0 and self.avg_cost != fill_price:
                self.avg_cost = fill_price

        if fill_date is not None:
            if fill_quantity > 0:
                self.add_buy_lot(fill_date, fill_quantity, fill_price)
            else:
                self.remove_sell_lots(abs(fill_quantity))

    def update_market_price(self, price: float) -> None:
        self.market_value = self.quantity * price
        if self.quantity != 0:
            self.unrealized_pnl = self.market_value - self.cost_basis
        else:
            self.unrealized_pnl = 0.0
