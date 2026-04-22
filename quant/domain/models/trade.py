from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, Optional


@dataclass(frozen=True)
class Trade:
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    side: str
    pnl: float = 0.0
    commission: float = 0.0
    realized_pnl: float = 0.0
    signal_date: Optional[datetime] = None
    fill_date: Optional[datetime] = None
    fill_price: float = 0.0
    intended_qty: float = 0.0
    cost_breakdown: Optional[Dict] = None
    strategy_name: Optional[str] = None

    @property
    def is_win(self) -> bool:
        return self.realized_pnl > 0

    @property
    def is_loss(self) -> bool:
        return self.realized_pnl < 0

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return self.pnl / (self.entry_price * abs(self.quantity)) * 100

    @property
    def duration_days(self) -> float:
        delta = self.exit_time - self.entry_time
        return delta.total_seconds() / 86400.0

    @classmethod
    def from_entry_exit(
        cls,
        symbol: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        entry_time: datetime,
        exit_time: datetime,
        side: str,
        commission: float = 0.0,
        strategy_name: Optional[str] = None,
        **kwargs,
    ) -> "Trade":
        if side == "buy":
            pnl = (exit_price - entry_price) * quantity - commission
        else:
            pnl = (entry_price - exit_price) * quantity - commission
        return cls(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            side=side,
            pnl=pnl,
            commission=commission,
            realized_pnl=pnl,
            strategy_name=strategy_name,
            **kwargs,
        )
