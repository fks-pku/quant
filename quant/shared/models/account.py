"""Account info model."""

from dataclasses import dataclass


@dataclass
class AccountInfo:
    """Account information from broker."""

    account_id: str
    cash: float
    buying_power: float
    equity: float
    margin_used: float