"""Interactive Brokers broker adapter (planned)."""

from typing import List, Optional

from quant.infrastructure.execution.brokers.base import BrokerAdapter, Order, OrderStatus, Position, AccountInfo


class IBKRBroker(BrokerAdapter):
    """Interactive Brokers adapter via ib_insync (planned)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        super().__init__("ibkr")
        self.host = host
        self.port = port
        self.client_id = client_id
        self._api = None

    def connect(self) -> None:
        """Connect to IBKR (planned)."""
        raise NotImplementedError("IBKR adapter not yet implemented")

    def disconnect(self) -> None:
        """Disconnect from IBKR."""
        if self._api:
            self._api.disconnect()
        self._connected = False

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def submit_order(self, order: Order) -> str:
        """Submit an order to IBKR (planned)."""
        raise NotImplementedError("IBKR adapter not yet implemented")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order (planned)."""
        raise NotImplementedError("IBKR adapter not yet implemented")

    def get_positions(self) -> List[Position]:
        """Get current positions (planned)."""
        raise NotImplementedError("IBKR adapter not yet implemented")

    def get_account_info(self) -> AccountInfo:
        """Get account information (planned)."""
        raise NotImplementedError("IBKR adapter not yet implemented")

    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get order status (planned)."""
        raise NotImplementedError("IBKR adapter not yet implemented")
