"""Domain exceptions — usable by any layer without creating cross-feature coupling."""

from enum import Enum, auto


class OrderRejectionReason(Enum):
    PRICE_UNRESOLVABLE = auto()
    DUPLICATE_BUY = auto()
    RISK_REJECTED = auto()
    BAR_UNAVAILABLE = auto()
    PRICE_INVALID = auto()
    PRICE_AT_LIMIT = auto()
    PRICE_DEVIATION = auto()
    INVALID_QUANTITY = auto()
    LOT_IMPOSSIBLE = auto()
    VOLUME_ZERO = auto()
    UNKNOWN_SIDE = auto()
    INSUFFICIENT_CASH = auto()
    NO_POSITION = auto()
    T1_SETTLEMENT = auto()


class OrderRejectedError(Exception):
    """Raised when an order cannot be executed, carrying the rejection reason."""

    def __init__(self, reason: OrderRejectionReason, symbol: str = "", detail: str = ""):
        self.reason = reason
        self.symbol = symbol
        self.detail = detail
        msg = f"[{reason.name}]"
        if symbol:
            msg += f" {symbol}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
