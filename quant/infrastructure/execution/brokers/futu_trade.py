"""Futu broker trade/order mixin — order submission, cancellation, queries."""

from typing import List, Optional

from quant.infrastructure.execution.brokers.base import Order, OrderStatus
from quant.infrastructure.execution.brokers.futu_connection import FutuOrderState


class FutuTradeMixin:
    """Order lifecycle methods for FutuBroker."""

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
