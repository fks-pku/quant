"""Order lifecycle management with routing and retry logic."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import hashlib
import json
import threading
import time
import uuid

from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.events.base import EventType, Event
from quant.shared.utils.logger import setup_logger


@dataclass
class OrderRequest:
    symbol: str
    quantity: float
    side: str
    order_type: str
    price: Optional[float] = None
    strategy_name: Optional[str] = None
    order_id: Optional[str] = None


class OrderManager:

    def __init__(
        self,
        portfolio: Any,
        risk_engine: Any,
        event_bus: EventPublisher,
        config: Dict[str, Any],
        strategy_tracker: Any = None,
        live_recorder: Any = None,
        risk_engine_resolver: Optional[Callable[[Optional[str]], Any]] = None,
    ):
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.event_bus = event_bus
        self.config = config
        self._strategy_tracker = strategy_tracker
        self._live_recorder = live_recorder
        self._risk_engine_resolver = risk_engine_resolver

        self._brokers: Dict[str, BrokerAdapter] = {}
        self._orders: Dict[str, Order] = {}
        self._symbol_to_broker: Dict[str, str] = {}
        self._lock = threading.RLock()
        execution_config = config.get("execution", {}) if isinstance(config, dict) else {}
        self._max_retries = int(execution_config.get("max_retries", 3) or 3)
        self._retry_delay = float(execution_config.get("retry_delay", 1.0) or 1.0)
        self._record_pending_only = bool(execution_config.get("record_pending_only", False))
        self._signal_timestamp: Optional[datetime] = None
        self._signal_submit_date: Optional[str] = None
        self._execution_timestamp: Optional[datetime] = None
        self._order_signal_metadata: Dict[str, Dict[str, Any]] = {}
        self.logger = setup_logger("OrderManager")

    def register_broker(self, name: str, broker: BrokerAdapter, symbols: Optional[List[str]] = None) -> None:
        """Register a broker adapter for order routing."""
        self._brokers[name] = broker
        if symbols:
            for symbol in symbols:
                self._symbol_to_broker[symbol] = name
        self.logger.info(f"Registered broker: {name}")

    def get_broker_for_symbol(self, symbol: str) -> BrokerAdapter:
        """Get the appropriate broker for a symbol."""
        broker_name = self._symbol_to_broker.get(symbol, "paper")
        broker = self._brokers.get(broker_name) or self._brokers.get("paper")
        if broker is None and len(self._brokers) == 1:
            broker = next(iter(self._brokers.values()))
        if broker is None:
            raise RuntimeError("No broker available")
        return broker

    def submit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        strategy_name: Optional[str] = None,
        risk_price: Optional[float] = None,
        signal_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Submit an order after risk checks.
        Returns order_id if successful, None if rejected.

        In record_pending_only mode, risk checks are deferred to D+1 submit;
        the signal is recorded as accepted without checking cash/position limits.
        """
        check_price = price
        if check_price is None:
            if risk_price is not None:
                check_price = risk_price
            else:
                check_price = self._get_last_price(symbol)
                price = check_price

        if self._record_pending_only:
            with self._lock:
                signal_timestamp = self._signal_timestamp or datetime.now()
                submit_date = self._signal_submit_date
            existing_order_id = self._existing_pending_live_signal_order_id(
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type=order_type,
                strategy_name=strategy_name,
                signal_timestamp=signal_timestamp,
                submit_date=submit_date,
            )
            if existing_order_id:
                with self._lock:
                    self._orders[existing_order_id] = Order(
                        symbol=symbol, quantity=quantity,
                        side=OrderSide(side), order_type=OrderType(order_type),
                        order_id=existing_order_id, status=OrderStatus.PENDING,
                        price=price, timestamp=signal_timestamp,
                        strategy_name=strategy_name,
                    )
                    self._remember_order_signal_metadata(existing_order_id, signal_metadata)
                self._record_strategy(existing_order_id, strategy_name)
                self.logger.info(
                    f"Order signal skipped as already pending: {existing_order_id} {symbol} {side} {quantity}"
                )
                return existing_order_id

            order_id = _make_pending_signal_order_id(
                strategy_name=strategy_name,
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type=order_type,
                signal_timestamp=signal_timestamp,
                submit_date=submit_date,
            )
            with self._lock:
                self._orders[order_id] = Order(
                    symbol=symbol, quantity=quantity,
                    side=OrderSide(side), order_type=OrderType(order_type),
                    order_id=order_id, status=OrderStatus.PENDING,
                    price=price, timestamp=signal_timestamp,
                    strategy_name=strategy_name,
                )
                self._remember_order_signal_metadata(order_id, signal_metadata)
            self._record_signal(
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type=order_type,
                price=price,
                strategy_name=strategy_name,
                status="accepted",
                order_id=order_id,
                signal_metadata=signal_metadata,
            )
            self._record_strategy(order_id, strategy_name)
            self.logger.info(
                f"Order signal recorded pending submission: {order_id} {symbol} {side} {quantity}"
            )
            return order_id

        order_value = abs(quantity * check_price)

        risk_engine = self._risk_engine_for(strategy_name)
        approved, results = risk_engine.check_order(
            symbol=symbol,
            quantity=quantity,
            price=check_price,
            order_value=order_value,
            side=side,
        )

        risk_engine.log_result(results)

        if not approved:
            self._record_signal(
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type=order_type,
                price=price,
                strategy_name=strategy_name,
                status="rejected",
                reason="risk_check_failed",
                signal_metadata=signal_metadata,
            )
            self.logger.warning(f"Order rejected by risk engine: {symbol} {side} {quantity}")
            self.event_bus.publish_nowait(
                EventType.ORDER_REJECTED,
                {
                    "symbol": symbol,
                    "quantity": quantity,
                    "side": side,
                    "reason": "risk_check_failed",
                }
            )
            return None

        order_id = str(uuid.uuid4())[:12].upper()
        order_timestamp = self._order_timestamp()

        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=OrderSide(side),
            order_type=OrderType(order_type),
            order_id=order_id,
            status=OrderStatus.PENDING,
            price=price,
            timestamp=order_timestamp,
            strategy_name=strategy_name,
        )

        with self._lock:
            self._orders[order_id] = order
            self._remember_order_signal_metadata(order_id, signal_metadata)

        self._record_signal(
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=order_type,
            price=price,
            strategy_name=strategy_name,
            status="accepted",
            order_id=order_id,
            signal_metadata=signal_metadata,
        )
        self._record_strategy(order_id, strategy_name)
        self._record_risk_order(risk_engine, symbol=symbol, order_value=order_value)
        self._submit_to_broker(order)

        return order_id

    def set_signal_timestamp(self, timestamp: Optional[datetime]) -> None:
        with self._lock:
            self._signal_timestamp = timestamp

    def clear_signal_timestamp(self) -> None:
        self.set_signal_timestamp(None)

    def set_signal_submit_date(self, submit_date: Optional[str]) -> None:
        with self._lock:
            self._signal_submit_date = str(submit_date)[:10] if submit_date else None

    def clear_signal_submit_date(self) -> None:
        self.set_signal_submit_date(None)

    def set_execution_timestamp(self, timestamp: Optional[datetime]) -> None:
        with self._lock:
            self._execution_timestamp = timestamp

    def clear_execution_timestamp(self) -> None:
        self.set_execution_timestamp(None)

    def _submit_to_broker(self, order: Order) -> None:
        """Submit order to broker with retry logic."""
        existing_order_id = self._existing_submitted_live_order_id(order)
        if existing_order_id:
            updated = replace(order, order_id=existing_order_id, status=OrderStatus.SUBMITTED)
            with self._lock:
                if order.order_id:
                    self._orders[order.order_id] = updated
                self._orders[existing_order_id] = updated
            self.logger.info(
                f"Order submit skipped as already recorded: {existing_order_id} {order.symbol} {order.side} {order.quantity}"
            )
            self._record_strategy(existing_order_id, order.strategy_name)
            return

        broker = self.get_broker_for_symbol(order.symbol)

        for attempt in range(self._max_retries):
            try:
                broker_order_id = broker.submit_order(order)
                updated = replace(order, order_id=broker_order_id, status=OrderStatus.SUBMITTED)
                with self._lock:
                    if order.order_id:
                        self._orders[order.order_id] = updated
                    self._orders[broker_order_id] = updated

                self.logger.info(f"Order submitted: {broker_order_id} {order.symbol} {order.side} {order.quantity}")
                self._record_strategy(broker_order_id, order.strategy_name)
                metadata = self._metadata_for_order(order.order_id)
                self._remember_order_signal_metadata(broker_order_id, metadata)
                self._record_order(updated, broker_order_id, "submitted", metadata=metadata)

                self.event_bus.publish_nowait(
                    EventType.ORDER_SUBMITTED,
                    {
                        "order_id": broker_order_id,
                        "symbol": order.symbol,
                        "quantity": order.quantity,
                        "side": order.side.value if hasattr(order.side, 'value') else order.side,
                    }
                )
                if hasattr(broker, "flush_trade_callbacks"):
                    broker.flush_trade_callbacks()
                return

            except Exception as e:
                self.logger.warning(f"Order submission attempt {attempt + 1} failed: {e}")
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2 ** attempt))

        rejected = replace(order, status=OrderStatus.REJECTED)
        with self._lock:
            self._orders[order.order_id] = rejected
        self._record_order(
            rejected,
            None,
            "rejected",
            reason="broker_submission_failed",
            metadata=self._metadata_for_order(order.order_id),
        )
        self.logger.error(f"Order rejected after {self._max_retries} attempts: {order.symbol}")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        with self._lock:
            if order_id not in self._orders:
                return False

            order = self._orders[order_id]
            broker = self.get_broker_for_symbol(order.symbol)

            try:
                success = broker.cancel_order(order.order_id or order_id)
                if success:
                    self._orders[order_id] = replace(order, status=OrderStatus.CANCELLED)
                    self._record_order(
                        self._orders[order_id],
                        order.order_id or order_id,
                        "cancelled",
                        metadata=self._metadata_for_order(order_id) or self._metadata_for_order(order.order_id),
                    )
                    self.logger.info(f"Order cancelled: {order_id}")
                    return True
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")

            return False

    def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        """Get the current status of an order."""
        with self._lock:
            order = self._orders.get(order_id)
            if order:
                return order.status
            return None

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        with self._lock:
            return self._orders.get(order_id)

    def get_all_orders(self) -> List[Order]:
        """Get all orders."""
        with self._lock:
            seen = set()
            result = []
            for o in self._orders.values():
                if o.order_id not in seen:
                    seen.add(o.order_id)
                    result.append(o)
            return result

    def get_open_orders(self) -> List[Order]:
        """Get all open (pending/submitted) orders."""
        with self._lock:
            seen = set()
            result = []
            for order in self._orders.values():
                if order.status not in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL):
                    continue
                if order.order_id in seen:
                    continue
                seen.add(order.order_id)
                result.append(order)
            return result

    def update_order_from_fill(
        self,
        order_id: str,
        filled_quantity: float,
        avg_fill_price: float,
    ) -> None:
        """Update order based on fill data."""
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                return

            if filled_quantity >= order.quantity:
                new_status = OrderStatus.FILLED
            else:
                new_status = OrderStatus.PARTIAL

            updated = replace(
                order,
                filled_quantity=filled_quantity,
                avg_fill_price=avg_fill_price,
                status=new_status,
            )
            for key, existing in list(self._orders.items()):
                if key == order_id or existing.order_id == order_id:
                    self._orders[key] = updated

    def _get_last_price(self, symbol: str) -> float:
        """Get last known price for a symbol.

        Raises ValueError if no price tracking is configured. Live/paper trading
        must register a price feed via register_price_feed() or pass explicit
        prices in order submission.
        """
        raise ValueError(
            f"No last price available for {symbol}. "
            "Market orders require a price; pass price= explicitly or configure a price feed."
        )

    def _risk_engine_for(self, strategy_name: Optional[str]) -> Any:
        if self._risk_engine_resolver is None:
            return self.risk_engine
        risk_engine = self._risk_engine_resolver(strategy_name)
        return risk_engine or self.risk_engine

    def _record_risk_order(self, risk_engine: Any, symbol: str, order_value: float) -> None:
        try:
            risk_engine.record_order(symbol=symbol, order_value=order_value)
        except TypeError:
            risk_engine.record_order()

    def _record_strategy(self, order_id: str, strategy_name: Optional[str]) -> None:
        if self._strategy_tracker is None:
            return
        try:
            self._strategy_tracker.record_order(order_id, strategy_name)
        except Exception:
            pass

    def _remember_order_signal_metadata(
        self,
        order_id: Optional[str],
        signal_metadata: Optional[Dict[str, Any]],
    ) -> None:
        if not order_id or not signal_metadata:
            return
        with self._lock:
            self._order_signal_metadata[str(order_id)] = dict(signal_metadata)

    def _metadata_for_order(self, order_id: Optional[str]) -> Dict[str, Any]:
        if not order_id:
            return {}
        with self._lock:
            return dict(self._order_signal_metadata.get(str(order_id), {}))

    def _record_signal(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str,
        price: Optional[float],
        strategy_name: Optional[str],
        status: str,
        order_id: Optional[str] = None,
        reason: str = "",
        signal_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._live_recorder is None:
            return
        try:
            with self._lock:
                timestamp = self._signal_timestamp or self._execution_timestamp or datetime.now()
                submit_date = self._signal_submit_date
            metadata = dict(signal_metadata or {})
            if submit_date and not metadata.get("submit_date") and not metadata.get("execution_date"):
                metadata["submit_date"] = submit_date
            self._live_recorder.record_signal(
                timestamp=timestamp,
                strategy_name=strategy_name,
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                price=price,
                status=status,
                order_id=order_id,
                reason=reason,
                metadata=metadata,
            )
        except Exception as e:
            self.logger.error(f"Failed to record strategy signal: {e}")

    def _record_order(
        self,
        order: Order,
        broker_order_id: Optional[str],
        status: str,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._live_recorder is None:
            return
        try:
            self._live_recorder.record_order(order, broker_order_id, status, reason=reason, metadata=metadata)
        except Exception as e:
            self.logger.error(f"Failed to record order: {e}")

    def _order_timestamp(self) -> datetime:
        with self._lock:
            return self._execution_timestamp or datetime.now()

    def _existing_submitted_live_order_id(self, order: Order) -> Optional[str]:
        if self._live_recorder is None:
            return None
        read_day = getattr(self._live_recorder, "read_day", None)
        if read_day is None:
            return None
        timestamp = order.timestamp or datetime.now()
        strategy_name = order.strategy_name or "default"
        try:
            rows = read_day("orders", timestamp.date().isoformat(), strategy_name=strategy_name)
        except Exception:
            return None
        side = order.side.value if hasattr(order.side, "value") else str(order.side)
        order_type = order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type)
        for row in reversed(rows):
            status = str(row.get("status") or "").lower()
            if status not in {"submitted", "partial", "filled"}:
                continue
            if str(row.get("symbol") or "") != order.symbol:
                continue
            if str(row.get("side") or "").upper() != side:
                continue
            if str(row.get("order_type") or "").upper() != order_type:
                continue
            try:
                if abs(float(row.get("quantity") or 0.0) - float(order.quantity)) > 1e-9:
                    continue
            except (TypeError, ValueError):
                continue
            return str(row.get("broker_order_id") or row.get("order_id") or "") or None
        return None

    def _existing_pending_live_signal_order_id(
        self,
        *,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str,
        strategy_name: Optional[str],
        signal_timestamp: datetime,
        submit_date: Optional[str],
    ) -> Optional[str]:
        if self._live_recorder is None:
            return None
        read_day = getattr(self._live_recorder, "read_day", None)
        if read_day is None:
            return None
        owner = strategy_name or "default"
        try:
            rows = read_day("signals", signal_timestamp.date().isoformat(), strategy_name=owner)
        except Exception:
            return None
        expected_side = str(side or "").upper()
        expected_order_type = str(order_type or "").upper()
        expected_submit_date = str(submit_date or "")[:10]
        for row in reversed(rows):
            status = str(row.get("status") or "").lower()
            if status not in {"accepted", "pending", "queued", "pending_submit"}:
                continue
            if str(row.get("symbol") or "") != symbol:
                continue
            if str(row.get("side") or "").upper() != expected_side:
                continue
            if str(row.get("order_type") or "").upper() != expected_order_type:
                continue
            if expected_submit_date and str(row.get("submit_date") or "")[:10] != expected_submit_date:
                continue
            if row.get("broker_order_id") or float(row.get("fill_quantity") or 0.0) > 0:
                continue
            try:
                if abs(float(row.get("quantity") or 0.0) - float(quantity)) > 1e-9:
                    continue
            except (TypeError, ValueError):
                continue
            order_id = str(row.get("order_id") or "")
            if order_id:
                return order_id
            return _make_pending_signal_order_id(
                strategy_name=strategy_name,
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type=order_type,
                signal_timestamp=signal_timestamp,
                submit_date=submit_date,
            )
        return None


def _make_pending_signal_order_id(
    *,
    strategy_name: Optional[str],
    symbol: str,
    quantity: float,
    side: str,
    order_type: str,
    signal_timestamp: datetime,
    submit_date: Optional[str],
) -> str:
    parts = [
        strategy_name or "default",
        signal_timestamp.date().isoformat(),
        str(submit_date or "")[:10],
        symbol,
        str(side or "").upper(),
        str(float(quantity)),
        str(order_type or "").upper(),
    ]
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return f"PEND-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8].upper()}"
