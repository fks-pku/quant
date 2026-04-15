"""Futu OpenAPI broker adapter for HK and US equities."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from quant.execution.brokers.base import AccountInfo, BrokerAdapter, Order, OrderStatus, Position
from quant.utils.logger import setup_logger


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


class FutuBroker(BrokerAdapter):
    """Futu OpenAPI broker adapter for HK and US stock trading."""

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
            from futu import OpenHKTradeContext, OpenQuoteContext

            self._trd_api = OpenHKTradeContext(host=self.host, port=self.port)
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
                    acc_name = acc.get("acc_name", "")
                    acc_type = acc.get("acc_type", "")
                    market = acc.get("market", "")
                    self.logger.info(f"  Account: {acc_id} ({acc_name}) - {acc_type} - Market: {market}")

                    if market:
                        self._acc_id_map[market] = int(acc_id)

        except Exception as e:
            self.logger.error(f"Error retrieving account list: {e}")

    def unlock_trade(self, password: Optional[str] = None, trade_mode: Optional[str] = None) -> bool:
        """
        Unlock trading with password.

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
            if ret != 0:
                self.logger.error(f"Failed to unlock trading: {data}")
                return False

            self._unlocked = True
            self._acc_id_map = {}
            for acc in self._acc_list:
                if isinstance(acc, dict):
                    market = acc.get("market", "")
                    if market:
                        self._acc_id_map[market] = int(acc.get("acc_id", 0))

            self.logger.info(f"Trading unlocked successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error unlocking trading: {e}")
            return False

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

    def _map_order_status(self, futu_status: str) -> OrderStatus:
        """Map Futu order status to system OrderStatus."""
        status_map = {
            "Submitted": OrderStatus.SUBMITTED,
            "Filled": OrderStatus.FILLED,
            "Partial": OrderStatus.PARTIAL,
            "Cancelled": OrderStatus.CANCELLED,
            "Rejected": OrderStatus.REJECTED,
            "Pending": OrderStatus.PENDING,
            "PendingSubmit": OrderStatus.PENDING,
            "Disabled": OrderStatus.CANCELLED,
            "Deleted": OrderStatus.CANCELLED,
        }
        return status_map.get(futu_status, OrderStatus.PENDING)

    def submit_order(self, order: Order) -> str:
        """
        Submit an order to Futu.

        Args:
            order: Order with symbol, quantity, side, order_type, price

        Returns:
            Futu order ID string, or empty string on failure
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return ""

        if not self._unlocked:
            self.logger.warning("Trading not unlocked. Attempting to unlock...")
            if not self.unlock_trade():
                self.logger.error("Failed to unlock trading")
                return ""

        market = self._get_market_from_symbol(order.symbol)
        acc_id = self._get_acc_id(market)
        if acc_id == 0:
            self.logger.error(f"No account found for market {market}")
            return ""

        trd_side_map = {
            "buy": "BUY",
            "sell": "SELL",
            "BUY": "BUY",
            "SELL": "SELL",
        }
        trd_side = trd_side_map.get(order.side.lower(), "BUY")

        if order.order_type.upper() == "MARKET":
            order_type = "NORMAL"
            price = 0.0
        elif order.order_type.upper() == "STOP":
            order_type = "STOP"
            price = order.price or 0.0
        else:
            order_type = "NORMAL"
            price = order.price or 0.0

        try:
            ret, data = self._trd_api.place_order(
                code=order.symbol,
                price=price,
                qty=order.quantity,
                side=trd_side,
                order_type=order_type,
                acc_id=acc_id,
            )

            if ret != 0:
                self.logger.error(f"Failed to place order: {data}")
                return ""

            order_id = str(data.get("order_id", ""))
            self.logger.info(f"Order placed: {order_id} - {order.symbol} {trd_side} {order.quantity} @ {price}")

            self._pending_orders[order_id] = FutuOrderState(
                order_id=order_id,
                symbol=order.symbol,
                side=trd_side,
                quantity=order.quantity,
                price=price,
                order_type=order_type,
                status=OrderStatus.SUBMITTED,
            )

            return order_id

        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            return ""

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Futu order ID to cancel

        Returns:
            True if cancellation was successful
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return False

        if not self._unlocked:
            self.logger.error("Trading not unlocked")
            return False

        if not order_id:
            self.logger.error("No order_id provided")
            return False

        try:
            ret, data = self._trd_api.cancel_order(order_id=order_id)
            if ret != 0:
                self.logger.error(f"Failed to cancel order {order_id}: {data}")
                return False

            self.logger.info(f"Order cancelled: {order_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error cancelling order {order_id}: {e}")
            return False

    def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
    ) -> bool:
        """
        Modify an existing order (change price and/or quantity).

        Args:
            order_id: Order ID to modify
            price: New price (None to keep current)
            quantity: New quantity (None to keep current)

        Returns:
            True if modification was successful
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return False

        if not self._unlocked:
            self.logger.error("Trading not unlocked")
            return False

        if not order_id:
            self.logger.error("No order_id provided")
            return False

        try:
            ret, data = self._trd_api.modify_order(
                order_id=order_id,
                price=price if price is not None else 0,
                qty=quantity if quantity is not None else 0,
            )

            if ret != 0:
                self.logger.error(f"Failed to modify order {order_id}: {data}")
                return False

            self.logger.info(f"Order modified: {order_id} - new price={price}, qty={quantity}")
            return True

        except Exception as e:
            self.logger.error(f"Error modifying order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        """
        Get the status of an order.

        Args:
            order_id: Futu order ID

        Returns:
            OrderStatus enum value
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return OrderStatus.PENDING

        if not order_id:
            return OrderStatus.PENDING

        if order_id in self._pending_orders:
            return self._pending_orders[order_id].status

        try:
            ret, data = self._trd_api.get_order_list()
            if ret != 0:
                self.logger.error(f"Failed to get order list: {data}")
                return OrderStatus.PENDING

            if data is None or data.empty:
                return OrderStatus.PENDING

            for _, row in data.iterrows():
                if str(row.get("order_id", "")) == str(order_id):
                    status_str = row.get("status", "Pending")
                    return self._map_order_status(status_str)

            return OrderStatus.PENDING

        except Exception as e:
            self.logger.error(f"Error getting order status for {order_id}: {e}")
            return OrderStatus.PENDING

    def get_positions(self) -> List[Position]:
        """
        Get current positions from Futu.

        Returns:
            List of Position dataclass objects
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return []

        if not self._unlocked:
            self.logger.error("Trading not unlocked")
            return []

        positions = []
        markets = ["HK", "US"] if len(self._acc_list) > 1 else [self._acc_list[0].get("market", "HK")] if self._acc_list else ["HK"]

        for market in markets:
            acc_id = self._get_acc_id(market)
            if acc_id == 0:
                continue

            try:
                ret, data = self._trd_api.position_list_query(acc_id=acc_id)
                if ret != 0:
                    self.logger.error(f"Failed to get positions for {market}: {data}")
                    continue

                if data is None or data.empty:
                    continue

                for _, row in data.iterrows():
                    symbol = row.get("code", "")
                    if not symbol:
                        continue

                    qty = float(row.get("position", 0))
                    if qty <= 0:
                        continue

                    avg_cost = float(row.get("cost_price", 0))
                    market_value = float(row.get("market_val", 0))
                    unrealized_pnl = float(row.get("unrealized_pl", 0))

                    positions.append(Position(
                        symbol=symbol,
                        quantity=qty,
                        avg_cost=avg_cost,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                    ))

            except Exception as e:
                self.logger.error(f"Error getting positions for {market}: {e}")

        self.logger.info(f"Retrieved {len(positions)} positions")
        return positions

    def get_account_info(self) -> AccountInfo:
        """
        Get account information (cash, buying power, equity).

        Args:
            market: Market to get info for ("HK" or "US"). Uses first available if None.

        Returns:
            AccountInfo dataclass
        """
        if not self._connected or not self._trd_api:
            self.logger.error("Not connected to Futu")
            return AccountInfo(
                account_id="",
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

        if not self._unlocked:
            self.logger.error("Trading not unlocked")
            return AccountInfo(
                account_id="",
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

        market = "HK"
        if self._acc_list:
            market = self._acc_list[0].get("market", "HK")

        acc_id = self._get_acc_id(market)
        if acc_id == 0:
            self.logger.error(f"No account found for market {market}")
            return AccountInfo(
                account_id="",
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

        try:
            ret, data = self._trd_api.accinfo_query(acc_id=acc_id)
            if ret != 0:
                self.logger.error(f"Failed to get account info: {data}")
                return AccountInfo(
                    account_id=str(acc_id),
                    cash=0.0,
                    buying_power=0.0,
                    equity=0.0,
                    margin_used=0.0,
                )

            if data is None or data.empty:
                return AccountInfo(
                    account_id=str(acc_id),
                    cash=0.0,
                    buying_power=0.0,
                    equity=0.0,
                    margin_used=0.0,
                )

            row = data.iloc[0]
            cash = float(row.get("cash", 0))
            buying_power = float(row.get("buying_power", 0))
            equity = float(row.get("total_assets", 0))
            margin_used = float(row.get("margin_used", 0))

            return AccountInfo(
                account_id=str(acc_id),
                cash=cash,
                buying_power=buying_power,
                equity=equity,
                margin_used=margin_used,
            )

        except Exception as e:
            self.logger.error(f"Error getting account info: {e}")
            return AccountInfo(
                account_id=str(acc_id),
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                margin_used=0.0,
            )

    def get_order_list(self) -> List[FutuOrderState]:
        """
        Get list of all orders.

        Returns:
            List of FutuOrderState objects
        """
        if not self._connected or not self._trd_api:
            return []

        orders = []
        markets = ["HK", "US"] if len(self._acc_list) > 1 else [self._acc_list[0].get("market", "HK")] if self._acc_list else ["HK"]

        for market in markets:
            acc_id = self._get_acc_id(market)
            if acc_id == 0:
                continue

            try:
                ret, data = self._trd_api.get_order_list(acc_id=acc_id)
                if ret != 0:
                    continue

                if data is None or data.empty:
                    continue

                for _, row in data.iterrows():
                    order_id = str(row.get("order_id", ""))
                    if not order_id:
                        continue

                    status = self._map_order_status(row.get("status", "Pending"))
                    orders.append(FutuOrderState(
                        order_id=order_id,
                        symbol=row.get("code", ""),
                        side=row.get("side", ""),
                        quantity=float(row.get("qty", 0)),
                        price=float(row.get("price", 0)),
                        order_type=row.get("order_type", "NORMAL"),
                        status=status,
                        filled_qty=float(row.get("fill_qty", 0)),
                        avg_fill_price=float(row.get("avg_price", 0)),
                    ))

            except Exception as e:
                self.logger.error(f"Error getting order list for {market}: {e}")

        return orders

    def get_today_deals(self) -> "pd.DataFrame":
        """
        Get today's executed trades (fills).

        Returns:
            DataFrame with today's trades
        """
        try:
            import pandas as pd
        except ImportError:
            import sys
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                from pandas import DataFrame
            return pd.DataFrame()

        if not self._connected or not self._trd_api:
            return pd.DataFrame()

        deals = []
        markets = ["HK", "US"] if len(self._acc_list) > 1 else [self._acc_list[0].get("market", "HK")] if self._acc_list else ["HK"]

        for market in markets:
            acc_id = self._get_acc_id(market)
            if acc_id == 0:
                continue

            try:
                ret, data = self._trd_api.deal_list_query(acc_id=acc_id)
                if ret != 0:
                    continue

                if data is None or data.empty:
                    continue

                deals.append(data)

            except Exception as e:
                self.logger.error(f"Error getting deals for {market}: {e}")

        if not deals:
            return pd.DataFrame()

        return pd.concat(deals, ignore_index=True)

    def get_order_fees(self, order_id: str) -> dict:
        """
        Get fee details for an order.

        Args:
            order_id: Futu order ID

        Returns:
            dict with fee breakdown
        """
        if not self._connected or not self._trd_api:
            return {}

        try:
            ret, data = self._trd_api.order_fee_query(order_id=order_id)
            if ret != 0:
                self.logger.error(f"Failed to get order fees: {data}")
                return {}
            return data if data else {}
        except Exception as e:
            self.logger.error(f"Error getting order fees: {e}")
            return {}

    def subscribe_order_updates(self, callback) -> None:
        """
        Subscribe to order update push notifications.

        Args:
            callback: Function to call on order updates
        """
        if not self._connected or not self._trd_api:
            return

        try:
            from futu import TradeOrderHandler

            class OrderUpdateHandler(TradeOrderHandler):
                def __init__(self, broker):
                    self.broker = broker

                def on_update(self, order_info):
                    order_id = str(order_info.get("order_id", ""))
                    if order_id in self.broker._pending_orders:
                        status_str = order_info.get("status", "Pending")
                        self.broker._pending_orders[order_id].status = self.broker._map_order_status(status_str)

            handler = OrderUpdateHandler(self)
            self._trd_api.set_order_handler(handler)
            self.logger.info("Subscribed to order updates")

        except Exception as e:
            self.logger.error(f"Error subscribing to order updates: {e}")

    def subscribe_acc_push(self) -> None:
        """Subscribe to account data push (positions, balance changes)."""
        if not self._connected or not self._trd_api:
            return

        try:
            ret = self._trd_api.sub_acc_push()
            if ret != 0:
                self.logger.error(f"Failed to subscribe to acc push")
            else:
                self.logger.info("Subscribed to account push data")

        except Exception as e:
            self.logger.error(f"Error subscribing to acc push: {e}")
