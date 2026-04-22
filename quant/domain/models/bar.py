from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    timeframe: str = "1d"
    source: Optional[str] = None
    adjusted: bool = False

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(
                f"high ({self.high}) must be >= max(O={self.open}, C={self.close}, L={self.low})"
            )
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(
                f"low ({self.low}) must be <= min(O={self.open}, C={self.close}, H={self.high})"
            )

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def weighted_price(self) -> float:
        if self.volume == 0:
            return self.typical_price
        return (self.high + self.low + self.close + self.close) / 4.0

    @property
    def value(self) -> float:
        return self.close * self.volume
