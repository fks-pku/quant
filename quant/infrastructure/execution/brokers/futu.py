"""Futu OpenAPI broker adapter for HK and US equities."""

from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.infrastructure.execution.brokers.futu_connection import (
    FutuConnectionMixin,
    FutuOrderState,
    FutuOrderType,
    TradeMode,
    TrdSide,
)
from quant.infrastructure.execution.brokers.futu_position import FutuPositionMixin
from quant.infrastructure.execution.brokers.futu_trade import FutuTradeMixin


class FutuBroker(FutuConnectionMixin, FutuTradeMixin, FutuPositionMixin, BrokerAdapter):
    """Futu OpenAPI broker adapter for HK and US stock trading."""
    pass
