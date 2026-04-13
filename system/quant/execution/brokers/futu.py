"""Futu broker adapter (planned)."""

from typing import List, Optional

from quant.execution.brokers.base import BrokerAdapter, Order, OrderStatus, Position, AccountInfo


class FutuBroker(BrokerAdapter):
    """Futu OpenAPI broker adapter (planned)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        super().__init__("futu")
        self.host = host
        self.port = port
        self._trd_api = None

    def connect(self) -> None:
        """Connect to Futu (planned)."""
        raise NotImplementedError("Futu broker adapter not yet implemented")

    def disconnect(self) -> None:
        """Disconnect from Futu."""
        if self._trd_api:
            self._trd_api.close()
        self._connected = False

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def submit_order(self, order: Order) -> str:
        """Submit an order to Futu (planned)."""
        raise NotImplementedError("Futu broker adapter not yet implemented")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order (planned)."""
        raise NotImplementedError("Futu broker adapter not yet implemented")

    def get_positions(self) -> List[Position]:
        """Get current positions (planned)."""
        raise NotImplementedError("Futu broker adapter not yet implemented")

    def get_account_info(self) -> AccountInfo:
        """Get account information (planned)."""
        raise NotImplementedError("Futu broker adapter not yet implemented")

    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get order status (planned)."""
        raise NotImplementedError("Futu broker adapter not yet implemented")
