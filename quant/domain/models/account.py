from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AccountInfo:
    account_id: str
    cash: float
    buying_power: float
    equity: float
    currency: str = "USD"
    margin_used: float = 0.0
    margin_available: float = 0.0
    day_trading_buying_power: Optional[float] = None
    maintenance_margin: float = 0.0

    @property
    def total_value(self) -> float:
        return self.equity

    @property
    def leverage(self) -> float:
        if self.equity <= 0:
            return 0.0
        return self.margin_used / self.equity
