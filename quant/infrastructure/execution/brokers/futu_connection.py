"""Futu broker connection mixin — enums, dataclass, connection lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from quant.infrastructure.execution.brokers.base import OrderStatus
from quant.shared.utils.logger import setup_logger


class TradeMode(Enum):
    """Trading mode enum."""
    SIMULATE = "simulate"
    REAL = "real"


class FutuOrderType(Enum):
    """Futu order types."""
    NORMAL = "normal"
    STOP = "stop"


class TrdSide(Enum):
    """Trade side."""
    BUY = "buy"
    SELL = "sell"


@dataclass
class FutuOrderState:
    """Futu order state mapping."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    order_type: str
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    update_time: Optional[datetime] = None


class FutuConnectionMixin:
    """Connection, authentication, and account-discovery methods for FutuBroker."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        acc_list: Optional[Dict[str, str]] = None,
        password: str = "",
        trade_mode: str = "SIMULATE",
    ):
        """
        Initialize Futu broker adapter.

        Args:
            host: OpenD host address
            port: OpenD port number
            acc_list: Dict mapping market to account ID, e.g., {"HK": "123456", "US": "789012"}
            password: Trading password (for unlocking real trading)
            trade_mode: "SIMULATE" for paper trading, "REAL" for live trading
        """
        super().__init__("futu")
        self.host = host
        self.port = port
        self.acc_list = acc_list or {}
        self.password = password
        self.trade_mode = TradeMode.SIMULATE if trade_mode == "SIMULATE" else TradeMode.REAL
        self.logger = setup_logger("FutuBroker")

        self._trd_api: Optional[Any] = None
        self._quote_api: Optional[Any] = None
        self._acc_list: List[Dict[str, Any]] = []
        self._acc_id_map: Dict[str, int] = {}
        self._unlocked: bool = False
        self._pending_orders: Dict[str, FutuOrderState] = {}

    def connect(self) -> None:
        """Connect to Futu OpenD and retrieve account list."""
        try:
            from futu import OpenSecTradeContext, OpenQuoteContext

            self._trd_api = OpenSecTradeContext(host=self.host, port=self.port)
            self._quote_api = OpenQuoteContext(host=self.host, port=self.port)
            self._connected = True
            self.logger.info(f"Connected to Futu OpenD at {self.host}:{self.port}")

            self._retrieve_account_list()

        except ImportError:
            self.logger.error("futu-api not installed. Install with: pip install futu-api")
            raise
        except Exception as e:
            self.logger.error(f"Failed to connect to Futu: {e}")
            raise

    def _retrieve_account_list(self) -> None:
        """Retrieve and display available trading accounts."""
        try:
            ret, data = self._trd_api.get_acc_list()
            if ret != 0:
                self.logger.error(f"Failed to get account list: {data}")
                return

            if hasattr(data, 'iterrows'):
                self._acc_list = data.to_dict('records')
            else:
                self._acc_list = data if isinstance(data, list) else []
            self.logger.info(f"Found {len(self._acc_list)} trading accounts")

            for acc in self._acc_list:
                if isinstance(acc, dict):
                    acc_id = acc.get("acc_id", "")
                    acc_type = acc.get("acc_type", "")
                    trd_env = acc.get("trd_env", "")
                    markets = acc.get("trdmarket_auth", [])
                    self.logger.info(f"  Account: {acc_id} - {acc_type} - {trd_env} - Markets: {markets}")

        except Exception as e:
            self.logger.error(f"Error retrieving account list: {e}")

    def unlock_trade(self, password: Optional[str] = None, trade_mode: Optional[str] = None) -> bool:
        """
        Unlock trading with password.

        For GUI OpenD: API unlock is not supported. The method detects GUI OpenD
        and checks if trading is already unlocked via a probe call (get_acc_list).
        For headless OpenD: uses the standard unlock_trade API.

        Args:
            password: Trading password. If None, uses self.password
            trade_mode: "SIMULATE" or "REAL". If None, uses self.trade_mode

        Returns:
            True if unlocked successfully
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return False

        pwd = password or self.password

        try:
            ret, data = self._trd_api.unlock_trade(password=pwd)
            if ret == 0:
                self._unlocked = True
                self._build_acc_id_map()
                self.logger.info("Trading unlocked successfully via API")
                return True
        except Exception:
            pass

        self._unlocked = self._probe_unlock_status()
        if self._unlocked:
            self._build_acc_id_map()
            self.logger.info("Trading already unlocked (GUI OpenD or pre-unlocked)")
        else:
            self.logger.warning("Trading not unlocked. For GUI OpenD, click unlock in OpenD interface first.")

        return self._unlocked

    def _probe_unlock_status(self) -> bool:
        """Probe whether trading is already unlocked by trying a read-only API call."""
        try:
            ret, data = self._trd_api.get_acc_list()
            if ret != 0:
                return False
            if hasattr(data, 'empty') and not data.empty:
                self._acc_list = data.to_dict('records')
                return True
            if isinstance(data, list) and len(data) > 0:
                self._acc_list = data
                return True
            return ret == 0
        except Exception:
            return False

    def _build_acc_id_map(self):
        """Build market -> acc_id mapping from account list."""
        self._acc_id_map = {}
        for acc in self._acc_list:
            if not isinstance(acc, dict):
                continue
            acc_id = int(acc.get("acc_id", 0))
            if acc_id == 0:
                continue

            markets = acc.get("trdmarket_auth", [])
            if isinstance(markets, list) and markets:
                for m in markets:
                    self._acc_id_map[m] = acc_id
            else:
                market = acc.get("market", "")
                if market:
                    self._acc_id_map[market] = acc_id

            acc_type = acc.get("acc_type", "")
            trd_env = acc.get("trd_env", "")
            if "HK" not in self._acc_id_map and trd_env in ("SIMULATE", "REAL"):
                self._acc_id_map["HK"] = acc_id
            if "US" not in self._acc_id_map and trd_env in ("SIMULATE", "REAL"):
                self._acc_id_map["US"] = acc_id

        self.logger.info(f"Account mapping: {self._acc_id_map}")

    def disconnect(self) -> None:
        """Disconnect from Futu OpenD."""
        if self._trd_api:
            self._trd_api.close()
            self._trd_api = None
        if self._quote_api:
            self._quote_api.close()
            self._quote_api = None
        self._connected = False
        self._unlocked = False
        self.logger.info("Disconnected from Futu OpenD")

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def is_unlocked(self) -> bool:
        """Check if trading is unlocked."""
        return self._unlocked

    def _get_market_from_symbol(self, symbol: str) -> str:
        """Extract market from symbol prefix."""
        if symbol.startswith("HK."):
            return "HK"
        elif symbol.startswith("US."):
            return "US"
        return "HK"

    def _get_trd_market(self, market: str) -> Any:
        """Get Futu TrdMarket enum for market string."""
        try:
            from futu import TrdMarket
            market_map = {
                "HK": TrdMarket.HK,
                "US": TrdMarket.US,
            }
            return market_map.get(market, TrdMarket.HK)
        except Exception:
            return market

    def _get_acc_id(self, market: str) -> int:
        """Get account ID for a market."""
        if not self._acc_list:
            return 0

        if market in self._acc_id_map:
            return self._acc_id_map[market]

        for acc in self._acc_list:
            if acc.get("market", "") == market:
                acc_id = int(acc.get("acc_id", 0))
                if acc_id > 0:
                    self._acc_id_map[market] = acc_id
                    return acc_id

        return int(self._acc_list[0].get("acc_id", 0)) if self._acc_list else 0
