import sys
import threading
import time
from dataclasses import fields as dc_fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from quant.domain.models.account import AccountInfo
from quant.domain.models.order import Order, OrderSide, OrderStatus, OrderType
from quant.domain.models.position import Position
from quant.domain.ports.broker import BrokerAdapter
from quant.shared.utils.logger import setup_logger


_SZ_PREFIXES = ("000", "001", "002", "003", "300", "301", "159", "184")
_BJ_PREFIXES = ("4", "8", "920")
_MARKET_PREFIXES = ("SH", "SZ", "BJ")


def _cn_symbol_to_qmt(symbol: str) -> str:
    raw = str(symbol).strip().upper()
    if raw.startswith("HK.") or raw.startswith("US."):
        return raw
    if "." in raw:
        left, right = raw.split(".", 1)
        if right in _MARKET_PREFIXES:
            return f"{left}.{right}"
        if left in _MARKET_PREFIXES:
            return f"{right}.{left}"
    if len(raw) >= 8 and raw[:2] in _MARKET_PREFIXES and raw[2:].isdigit():
        return f"{raw[2:]}.{raw[:2]}"
    code = "".join(ch for ch in raw if ch.isdigit())
    if code.startswith(_BJ_PREFIXES):
        return f"{code}.BJ"
    if code.startswith(_SZ_PREFIXES):
        return f"{code}.SZ"
    return f"{code}.SH"


def _qmt_symbol_to_cn(symbol: str) -> str:
    raw = str(symbol).strip().upper()
    if "." in raw:
        left, right = raw.split(".", 1)
        if left in _MARKET_PREFIXES:
            return right
        return left
    if len(raw) >= 8 and raw[:2] in _MARKET_PREFIXES and raw[2:].isdigit():
        return raw[2:]
    return raw


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    return float(val)


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    return int(val)


def _get_field(record: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(record, dict) and name in record:
            value = record[name]
            if value is not None:
                return value
        if hasattr(record, "get"):
            try:
                value = record.get(name, None)
                if value is not None:
                    return value
            except Exception:
                pass
        if hasattr(record, name):
            value = getattr(record, name)
            if value is not None:
                return value
    return default


def _records(data: Any) -> List[Any]:
    if data is None:
        return []
    if hasattr(data, "empty") and data.empty:
        return []
    if hasattr(data, "iterrows"):
        return [row for _, row in data.iterrows()]
    if isinstance(data, (list, tuple)):
        return list(data)
    return [data]


def _constant(module: Any, name: str, fallback: int) -> int:
    return int(getattr(module, name, fallback))


def _order_identifier(data: Any) -> str:
    value = _get_field(data, "order_id", "m_nOrderID", "m_strOrderSysID", "order_sysid", default="")
    return str(value) if value is not None else ""


class QMTBroker(BrokerAdapter):

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 58610,
        account: str = "",
        trade_mode: str = "SIMULATE",
        password: str = "",
        mini_qmt_path: str = "",
        userdata_mini_path: str = "",
        xtquant_path: str = "",
        account_type: str = "STOCK",
        session_id: Optional[int] = None,
    ):
        super().__init__("qmt")
        self._host = host
        self._port = port
        self._account_id = str(account).strip()
        self._account_type = account_type.upper()
        self._trade_mode = trade_mode.upper()
        self._password = password
        self._userdata_mini_path = userdata_mini_path or mini_qmt_path
        self._xtquant_path = xtquant_path
        if not xtquant_path and mini_qmt_path and "site-packages" in mini_qmt_path.lower():
            self._xtquant_path = mini_qmt_path
            self._userdata_mini_path = ""
        self._session_id = session_id or int(time.time() % 1_000_000)

        self._trader: Any = None
        self._account: Any = None
        self._xtconstant: Any = None
        self._pending_orders: Dict[str, Order] = {}
        self._trade_callbacks: List[Callable[..., None]] = []
        self._positions_cache: Dict[str, Position] = {}
        self._account_info_cache: Optional[AccountInfo] = None
        self._lock = threading.RLock()
        self.logger = setup_logger("QMTBroker")

    def connect(self) -> None:
        self._extend_sys_path()
        xttrader, StockAccount, xtconstant = self._import_xtquant()
        self._xtconstant = xtconstant
        if not self._userdata_mini_path:
            raise RuntimeError(
                "QMT userdata_mini_path is required. Set qmt.userdata_mini_path "
                "to the MiniQMT userdata_mini directory."
            )

        callback = self._make_callback(xttrader.XtQuantTraderCallback)
        self._trader = xttrader.XtQuantTrader(self._userdata_mini_path, self._session_id)
        if hasattr(self._trader, "register_callback"):
            self._trader.register_callback(callback)
        self._trader.start()
        conn_result = self._trader.connect()
        if conn_result != 0:
            self._connected = False
            raise ConnectionError(
                f"QMT connection failed (code={conn_result}). "
                "Ensure MiniQMT is running and logged in."
            )

        if not self._account_id:
            self._account_id = self._detect_account_id()
        if not self._account_id:
            raise RuntimeError("No QMT account found. Specify qmt.account in brokers.yaml")

        self._account = StockAccount(self._account_id, self._account_type)
        if hasattr(self._trader, "subscribe"):
            subscribe_result = self._trader.subscribe(self._account)
            if subscribe_result != 0:
                raise RuntimeError(f"QMT account subscribe failed (code={subscribe_result})")

        if self._trade_mode == "REAL" and self._password:
            self._unlock_trade()

        with self._lock:
            self._connected = True
            self._refresh_account()
            self._refresh_positions()
        self.logger.info(f"QMT broker connected (account={self._account_id}, mode={self._trade_mode})")

    def _extend_sys_path(self) -> None:
        for raw_path in (self._xtquant_path, self._userdata_mini_path):
            if not raw_path:
                continue
            path = Path(raw_path)
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

    def _import_xtquant(self):
        try:
            from xtquant import xtconstant, xttrader
            from xtquant.xttype import StockAccount
        except ImportError as exc:
            raise ImportError(
                "xtquant not found. Install/use the Python environment bundled with QMT, "
                "or set qmt.xtquant_path to the QMT bin.x64/Lib/site-packages directory."
            ) from exc
        return xttrader, StockAccount, xtconstant

    def _make_callback(self, base_callback: Any) -> Any:
        broker_ref = self

        class _Callback(base_callback):
            def on_disconnected(inner_self):
                broker_ref.logger.warning("QMT connection lost")
                with broker_ref._lock:
                    broker_ref._connected = False

            def on_stock_order(inner_self, order):
                _qmt_order_callback(broker_ref, order)

            def on_stock_trade(inner_self, trade):
                _qmt_trade_callback(broker_ref, trade)

            def on_order_error(inner_self, err):
                _qmt_order_error_callback(broker_ref, err)

            def on_cancel_error(inner_self, err):
                _qmt_cancel_error_callback(broker_ref, err)

            def on_stock_asset(inner_self, asset):
                _qmt_asset_callback(broker_ref, asset)

            def on_stock_position(inner_self, position):
                _qmt_position_callback(broker_ref, position)

        return _Callback()

    def _detect_account_id(self) -> str:
        for method_name in ("query_account_infos", "query_accounts"):
            if not hasattr(self._trader, method_name):
                continue
            try:
                result = getattr(self._trader, method_name)()
            except Exception:
                continue
            for item in _records(result):
                value = _get_field(item, "account_id", "m_strAccountID", default=item if isinstance(item, str) else "")
                if value:
                    return str(value)
        return ""

    def _unlock_trade(self) -> None:
        try:
            if hasattr(self._trader, "unlock_trade"):
                self._trader.unlock_trade(self._password)
            else:
                self.logger.warning("QMT auto-unlock not available. Unlock trading in QMT client manually.")
            self.logger.info("QMT trading unlocked")
        except Exception as e:
            self.logger.warning(f"QMT unlock failed: {e}. Unlock manually in QMT client.")

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            if self._trader:
                try:
                    if self._account and hasattr(self._trader, "unsubscribe"):
                        self._trader.unsubscribe(self._account)
                    self._trader.stop()
                except Exception:
                    pass
                self._trader = None
            self.logger.info("QMT broker disconnected")

    def is_connected(self) -> bool:
        return self._connected

    def register_trade_callback(self, callback: Callable[..., None]) -> None:
        with self._lock:
            self._trade_callbacks.append(callback)

    def submit_order(self, order: Order) -> str:
        with self._lock:
            self._ensure_connected()

            qmt_code = _cn_symbol_to_qmt(order.symbol)
            volume = int(order.quantity)
            if volume <= 0:
                raise ValueError(f"QMT order quantity must be positive: {order.quantity}")

            if order.side == OrderSide.SELL:
                order_type = _constant(self._xtconstant, "STOCK_SELL", 24)
            else:
                order_type = _constant(self._xtconstant, "STOCK_BUY", 23)
            if order.order_type == OrderType.MARKET:
                price_type = _constant(self._xtconstant, "LATEST_PRICE", 5)
                price = 0.0
            else:
                price_type = _constant(self._xtconstant, "FIX_PRICE", 11)
                price = float(order.price or 0.0)
            strategy_name = (getattr(order, "strategy_name", "") or "quant")[:32]
            order_remark = ""

            self.logger.info(
                f"QMT order: {order.side.value} {volume} {qmt_code} "
                f"type={order.order_type.value} price={price}"
            )

            result = self._trader.order_stock(
                self._account,
                qmt_code,
                order_type,
                volume,
                price_type,
                price,
                strategy_name,
                order_remark,
            )

            if result is None or not isinstance(result, int) or result <= 0:
                raise RuntimeError(f"QMT order_stock failed for {order.symbol}: result={result}")

            order_id = str(result)
            updated = self._set_order_attrs(order, {
                "order_id": order_id,
                "status": OrderStatus.SUBMITTED,
                "timestamp": datetime.now(),
            })
            self._pending_orders[order_id] = updated
            return order_id

    def cancel_order(self, order_id: str) -> bool:
        with self._lock:
            self._ensure_connected()
            qmt_order_id = _safe_int(order_id, 0) if str(order_id).isdigit() else order_id
            result = self._trader.cancel_order_stock(self._account, qmt_order_id)
            if result != 0:
                self.logger.error(f"QMT cancel failed: order_id={order_id}, result={result}")
                return False
            self.logger.info(f"QMT cancel request sent: order_id={order_id}")
            return True

    def get_order_status(self, order_id: str) -> OrderStatus:
        with self._lock:
            if order_id in self._pending_orders:
                return self._pending_orders[order_id].status
            return OrderStatus.REJECTED

    def get_positions(self) -> List[Position]:
        with self._lock:
            self._refresh_positions()
            return [p for p in self._positions_cache.values() if p.quantity > 0]

    def get_account_info(self) -> AccountInfo:
        with self._lock:
            self._refresh_account()
            if self._account_info_cache:
                return self._account_info_cache
            return AccountInfo(
                account_id=self._account_id,
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
                currency="CNY",
            )

    def _refresh_positions(self) -> None:
        if not self._connected or not self._trader or not self._account:
            return
        try:
            data = self._trader.query_stock_positions(self._account)
            records = _records(data)
            if data is not None:
                self._positions_cache.clear()
            for record in records:
                pos = self._position_from_record(record)
                if pos and pos.quantity > 0:
                    self._positions_cache[pos.symbol] = pos
        except Exception as e:
            self.logger.warning(f"QMT position query failed: {e}")

    def _position_from_record(self, record: Any) -> Optional[Position]:
        qmt_code = _get_field(record, "stock_code", "m_strStockCode", "m_strInstrumentID", "instrument_id", default="")
        code = _qmt_symbol_to_cn(qmt_code)
        if not code:
            return None
        qty = _safe_float(_get_field(record, "volume", "m_nVolume", "totalAmt", default=0.0))
        if qty <= 0:
            return None
        avg_cost = _safe_float(_get_field(record, "avg_price", "open_price", "cost_price", "m_dOpenPrice", "costPrice", default=0.0))
        market_value = _safe_float(_get_field(record, "market_value", "m_dMarketValue", "marketValue", default=0.0))
        pnl = _safe_float(_get_field(record, "position_profit", "float_profit", "profit", "income", "m_dPositionProfit", "m_dFloatProfit", default=0.0))
        return Position(
            symbol=code,
            quantity=qty,
            avg_cost=avg_cost,
            market_value=market_value,
            unrealized_pnl=pnl,
        )

    def _refresh_account(self) -> None:
        if not self._connected or not self._trader or not self._account:
            return
        try:
            asset = None
            if self._account_type == "CREDIT" and hasattr(self._trader, "query_credit_detail"):
                asset = self._trader.query_credit_detail(self._account)
            if asset is None:
                asset = self._trader.query_stock_asset(self._account)
            account_info = self._account_info_from_asset(asset)
            if account_info:
                self._account_info_cache = account_info
        except Exception as e:
            self.logger.warning(f"QMT account query failed: {e}")

    def _account_info_from_asset(self, asset: Any) -> Optional[AccountInfo]:
        records = _records(asset)
        if not records:
            return None
        record = records[0]
        account_id = str(_get_field(record, "account_id", "m_strAccountID", default=self._account_id))
        cash = _safe_float(_get_field(record, "cash", "m_dCash", "m_dAvailable", default=0.0))
        market_value = _safe_float(_get_field(record, "market_value", "m_dMarketValue", default=0.0))
        total_asset = _safe_float(_get_field(record, "total_asset", "m_dTotalAsset", "m_dBalance", default=0.0))
        if total_asset <= 0:
            total_asset = cash + market_value
        frozen_cash = _safe_float(_get_field(record, "frozen_cash", "m_dFrozenCash", default=0.0))
        return AccountInfo(
            account_id=account_id,
            cash=cash,
            buying_power=cash,
            equity=total_asset,
            currency="CNY",
            margin_used=0.0,
            margin_available=cash,
            maintenance_margin=frozen_cash,
        )

    def _ensure_connected(self) -> None:
        if not self._connected or not self._trader or not self._account:
            raise RuntimeError("QMT broker not connected")

    def _set_order_attrs(self, order: Order, updates: dict) -> Order:
        frozen = getattr(order, "__dataclass_params__", None)
        if frozen and frozen.frozen:
            kwargs = {}
            for f in dc_fields(order):
                kwargs[f.name] = updates.get(f.name, getattr(order, f.name))
            return Order(**kwargs)
        for k, v in updates.items():
            try:
                object.__setattr__(order, k, v)
            except (AttributeError, TypeError):
                pass
        return order

    def _notify_trade_callbacks(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_name: Optional[str],
        timestamp: datetime,
    ) -> None:
        callbacks = list(self._trade_callbacks)
        for callback in callbacks:
            try:
                callback(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    timestamp=timestamp,
                    strategy_name=strategy_name,
                )
            except Exception as e:
                self.logger.error(f"QMT trade callback failed: {e}")


_ORDER_STATUS_MAP = {
    48: OrderStatus.PENDING,
    49: OrderStatus.PENDING,
    50: OrderStatus.SUBMITTED,
    51: OrderStatus.SUBMITTED,
    52: OrderStatus.PARTIAL,
    53: OrderStatus.CANCELLED,
    54: OrderStatus.CANCELLED,
    55: OrderStatus.PARTIAL,
    56: OrderStatus.FILLED,
    57: OrderStatus.REJECTED,
}


def _qmt_order_callback(broker: QMTBroker, order: Any) -> None:
    order_id = _order_identifier(order)
    qmt_status = _get_field(order, "order_status", "m_nOrderStatus", default=0)
    mapped = _ORDER_STATUS_MAP.get(qmt_status, OrderStatus.SUBMITTED)
    with broker._lock:
        if order_id in broker._pending_orders:
            existing = broker._pending_orders[order_id]
            broker._pending_orders[order_id] = broker._set_order_attrs(existing, {"status": mapped})


def _qmt_trade_callback(broker: QMTBroker, trade: Any) -> None:
    order_id = _order_identifier(trade)
    fill_price = _safe_float(_get_field(trade, "traded_price", "m_dPrice", default=0.0))
    fill_qty = _safe_float(_get_field(trade, "traded_volume", "m_nVolume", default=0.0))
    fill_ts = datetime.now()
    symbol = _qmt_symbol_to_cn(_get_field(trade, "stock_code", "m_strStockCode", "m_strInstrumentID", default=""))
    side = ""
    strategy_name = None
    with broker._lock:
        if order_id in broker._pending_orders:
            existing = broker._pending_orders[order_id]
            symbol = existing.symbol or symbol
            side = existing.side.value if hasattr(existing.side, "value") else str(existing.side)
            strategy_name = existing.strategy_name
            filled_qty = existing.filled_quantity + fill_qty
            avg_price = fill_price
            if existing.filled_quantity > 0 and existing.avg_fill_price:
                total_cost = existing.avg_fill_price * existing.filled_quantity + fill_price * fill_qty
                avg_price = total_cost / filled_qty if filled_qty > 0 else fill_price
            status = OrderStatus.FILLED if filled_qty >= existing.quantity else OrderStatus.PARTIAL
            broker._pending_orders[order_id] = broker._set_order_attrs(
                existing,
                {"filled_quantity": filled_qty, "avg_fill_price": avg_price, "status": status},
            )
    if fill_qty > 0 and fill_price > 0:
        broker._notify_trade_callbacks(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=fill_qty,
            price=fill_price,
            strategy_name=strategy_name,
            timestamp=fill_ts,
        )
    broker.logger.info(f"QMT trade: {fill_qty} @ {fill_price}, order={order_id}")


def _qmt_order_error_callback(broker: QMTBroker, err: Any) -> None:
    order_id = _order_identifier(err)
    error_msg = str(_get_field(err, "error_msg", "m_strErrorInfo", "error_info", default=""))
    with broker._lock:
        if order_id and order_id in broker._pending_orders:
            existing = broker._pending_orders[order_id]
            broker._pending_orders[order_id] = broker._set_order_attrs(existing, {"status": OrderStatus.REJECTED})
    broker.logger.error(f"QMT order error: id={order_id}, msg={error_msg}")


def _qmt_cancel_error_callback(broker: QMTBroker, err: Any) -> None:
    broker.logger.error(f"QMT cancel error: {err}")


def _qmt_asset_callback(broker: QMTBroker, asset: Any) -> None:
    info = broker._account_info_from_asset(asset)
    if not info:
        return
    with broker._lock:
        broker._account_info_cache = info


def _qmt_position_callback(broker: QMTBroker, position: Any) -> None:
    pos = broker._position_from_record(position)
    code = pos.symbol if pos else _qmt_symbol_to_cn(_get_field(position, "stock_code", "m_strStockCode", "m_strInstrumentID", default=""))
    with broker._lock:
        if pos and pos.quantity > 0:
            broker._positions_cache[code] = pos
        elif code:
            broker._positions_cache.pop(code, None)
