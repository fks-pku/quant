import sys
import threading
import time
from dataclasses import fields as dc_fields
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
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
_QMT_CN_FUND_PREFIXES = ("15", "16", "18", "50", "51", "52", "56", "58")
_QMT_CN_DEFAULT_COMMISSION_RATE = 0.00025
_QMT_CN_MAX_COMMISSION_RATE = 0.003
_QMT_CN_MIN_COMMISSION = 5.0


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
    if isinstance(val, (list, tuple)):
        val = val[0] if val else default
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


def _safe_price_field(record: Any, *names: str) -> float:
    try:
        return _safe_float(_get_field(record, *names, default=0.0))
    except (TypeError, ValueError):
        return 0.0


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


def _qmt_limit_price_tick(qmt_code: str) -> Decimal:
    raw = str(qmt_code).strip().upper()
    code, market = raw.split(".", 1) if "." in raw else (raw, "")
    if market == "SH" and code.startswith("5"):
        return Decimal("0.001")
    if market == "SZ" and code.startswith(("15", "16", "184")):
        return Decimal("0.001")
    return Decimal("0.01")


def _normalize_qmt_limit_price(qmt_code: str, side: OrderSide, price: float) -> float:
    value = Decimal(str(price))
    if value <= 0:
        return 0.0
    tick = _qmt_limit_price_tick(qmt_code)
    rounding = ROUND_CEILING if side == OrderSide.SELL else ROUND_FLOOR
    normalized = (value / tick).to_integral_value(rounding=rounding) * tick
    if normalized <= 0:
        raise ValueError(f"QMT limit price too small for {qmt_code}: {price}")
    return float(normalized)


def _order_identifier(data: Any) -> str:
    value = _get_field(data, "order_id", "m_nOrderID", "m_strOrderSysID", "order_sysid", default="")
    return str(value) if value is not None else ""


def _trade_identifier(data: Any) -> str:
    value = _get_field(data, "trade_id", "fill_id", "m_strTradeID", "deal_id", default="")
    return str(value) if value is not None else ""


def _qmt_side_from_record(record: Any) -> str:
    raw = _get_field(record, "side", "direction", "order_side", "order_type", "entrust_bs", "m_nOrderType", default="")
    text = str(raw).upper()
    if text in {"BUY", "B", "23", "STOCK_BUY"}:
        return "BUY"
    if text in {"SELL", "S", "24", "STOCK_SELL"}:
        return "SELL"
    return ""


def _qmt_timestamp_from_record(record: Any) -> Optional[str]:
    value = _get_field(record, "timestamp", "traded_time", "trade_time", "m_strTradeTime", "order_time", default=None)
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip().replace(" ", "T")
    if text.isdigit():
        try:
            epoch_seconds = int(text)
            if 946684800 <= epoch_seconds <= 4102444800:
                return datetime.fromtimestamp(epoch_seconds).isoformat()
        except (OverflowError, ValueError):
            pass
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text


def _commission_config_for_market(commission_config: Any, market: str) -> Dict[str, Any]:
    if isinstance(commission_config, dict):
        value = commission_config.get(market, commission_config)
        return value if isinstance(value, dict) else {}
    value = getattr(commission_config, market, None)
    return value if isinstance(value, dict) else {}


def _is_qmt_cn_fund_symbol(symbol: str) -> bool:
    code = _qmt_symbol_to_cn(symbol)
    return code.isdigit() and len(code) == 6 and code.startswith(_QMT_CN_FUND_PREFIXES)


def _configured_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        commission_config: Optional[Dict[str, Any]] = None,
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
        self._commission_config = commission_config or {}
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
        self._quote_cache: Dict[str, Dict[str, float]] = {}
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
            if self._trade_mode != "REAL":
                raise RuntimeError(
                    "QMT trade_mode=SIMULATE is not a verified sandbox order route; "
                    "use PaperBroker for paper trading or set trade_mode=REAL for confirmed live orders."
                )

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
                price = _normalize_qmt_limit_price(qmt_code, order.side, float(order.price or 0.0))
            strategy_name = (getattr(order, "strategy_name", "") or "quant")[:32]
            order_remark = ""
            trader = self._trader
            account = self._account

        self.logger.info(
            f"QMT order: {order.side.value} {volume} {qmt_code} "
            f"type={order.order_type.value} price={price}"
        )

        result = trader.order_stock(
            account,
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
        with self._lock:
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

    def get_trade_history(self, start_date: Any = None, end_date: Any = None) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_connected()
            records = self._query_history(
                ("query_stock_trades", "query_trades", "query_stock_trade"),
                start_date=start_date,
                end_date=end_date,
            )
            return [row for row in (self._trade_history_row(record) for record in records) if row]

    def get_order_history(self, start_date: Any = None, end_date: Any = None) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_connected()
            records = self._query_history(
                ("query_stock_orders", "query_orders", "query_stock_order"),
                start_date=start_date,
                end_date=end_date,
            )
            return [row for row in (self._order_history_row(record) for record in records) if row]

    def get_quote(self, symbol: str) -> Optional[Dict[str, float]]:
        self._ensure_connected()
        qmt_code = _cn_symbol_to_qmt(symbol)
        try:
            from xtquant import xtdata
            ticks = xtdata.get_full_tick([qmt_code])
        except Exception as e:
            self.logger.warning(f"QMT quote query failed for {symbol}: {e}")
            return self._quote_cache.get(_qmt_symbol_to_cn(qmt_code))
        record = None
        if isinstance(ticks, dict):
            record = ticks.get(qmt_code) or ticks.get(symbol)
        else:
            records = _records(ticks)
            record = records[0] if records else None
        quote = self._quote_from_record(record)
        if quote:
            with self._lock:
                self._quote_cache[_qmt_symbol_to_cn(qmt_code)] = quote
        return quote

    def get_execution_reference_price(self, symbol: str, side: Optional[str] = None) -> Optional[Dict[str, float]]:
        return self.get_quote(symbol)

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

    def _quote_from_record(self, record: Any) -> Optional[Dict[str, float]]:
        if record is None:
            return None
        quote = {
            "open_price": _safe_price_field(record, "open", "openPrice", "open_price"),
            "last_price": _safe_price_field(record, "lastPrice", "last_price", "price"),
            "bid_price": _safe_price_field(record, "bidPrice", "bid_price", "bid1", "bid1Price"),
            "ask_price": _safe_price_field(record, "askPrice", "ask_price", "ask1", "ask1Price"),
        }
        quote = {key: value for key, value in quote.items() if value > 0}
        return quote or None

    def _query_history(self, method_names: tuple, start_date: Any = None, end_date: Any = None) -> List[Any]:
        if not self._trader or not self._account:
            return []
        start_text = start_date.isoformat() if hasattr(start_date, "isoformat") else start_date
        end_text = end_date.isoformat() if hasattr(end_date, "isoformat") else end_date
        for method_name in method_names:
            if not hasattr(self._trader, method_name):
                continue
            method = getattr(self._trader, method_name)
            attempts = [
                (self._account, start_text, end_text),
                (self._account,),
                tuple(),
            ]
            for args in attempts:
                try:
                    return _records(method(*args))
                except TypeError:
                    continue
                except Exception as e:
                    self.logger.warning(f"QMT history query failed via {method_name}: {e}")
                    return []
        return []

    def _trade_history_row(self, record: Any) -> Optional[Dict[str, Any]]:
        order_id = _order_identifier(record)
        symbol = _qmt_symbol_to_cn(_get_field(record, "symbol", "stock_code", "m_strStockCode", "m_strInstrumentID", default=""))
        side = _qmt_side_from_record(record)
        quantity = _safe_float(_get_field(record, "quantity", "traded_volume", "m_nVolume", "volume", default=0.0))
        price = _safe_float(_get_field(record, "price", "traded_price", "m_dPrice", default=0.0))
        if not order_id or not symbol or not side or quantity <= 0 or price <= 0:
            return None
        return {
            "order_id": order_id,
            "trade_id": _trade_identifier(record),
            "timestamp": _qmt_timestamp_from_record(record),
            "strategy_name": str(_get_field(record, "strategy_name", "strategy", "order_remark", "remark", default="") or ""),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "commission": self._commission_from_trade_record(record),
        }

    def _order_history_row(self, record: Any) -> Optional[Dict[str, Any]]:
        order_id = _order_identifier(record)
        symbol = _qmt_symbol_to_cn(_get_field(record, "symbol", "stock_code", "m_strStockCode", "m_strInstrumentID", default=""))
        if not order_id or not symbol:
            return None
        return {
            "order_id": order_id,
            "timestamp": _qmt_timestamp_from_record(record),
            "strategy_name": str(_get_field(record, "strategy_name", "strategy", "order_remark", "remark", default="") or ""),
            "symbol": symbol,
            "side": _qmt_side_from_record(record),
            "quantity": _safe_float(_get_field(record, "quantity", "order_volume", "m_nVolume", "volume", default=0.0)),
            "price": _safe_float(_get_field(record, "price", "order_price", "m_dPrice", default=0.0)),
            "status": str(_get_field(record, "status", "order_status", "m_nOrderStatus", default="")),
        }

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
        commission: float,
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
                    commission=commission,
                    timestamp=timestamp,
                    strategy_name=strategy_name,
                )
            except Exception as e:
                self.logger.error(f"QMT trade callback failed: {e}")

    def _commission_from_trade_record(self, trade: Any) -> float:
        value = _get_field(
            trade,
            "commission",
            "m_dCommission",
            "entrust_fee",
            "m_dEntrustFee",
            "fee",
            "m_dFee",
            default=None,
        )
        try:
            commission = float(value)
        except (TypeError, ValueError):
            return 0.0
        return commission if commission > 0 else 0.0

    def estimate_commission(self, symbol: str, side: str, quantity: float, price: float) -> float:
        code = _qmt_symbol_to_cn(symbol)
        if not (code.isdigit() and len(code) == 6):
            return 0.0
        trade_value = abs(float(quantity) * float(price))
        if trade_value <= 0:
            return 0.0
        cfg = _commission_config_for_market(self._commission_config, "CN")
        if _is_qmt_cn_fund_symbol(code):
            raw_rate = cfg.get("fund_percent", cfg.get("percent", _QMT_CN_DEFAULT_COMMISSION_RATE))
            raw_min = cfg.get("fund_min_per_order", cfg.get("min_per_order", _QMT_CN_MIN_COMMISSION))
        else:
            raw_rate = cfg.get("percent", _QMT_CN_DEFAULT_COMMISSION_RATE)
            raw_min = cfg.get("min_per_order", _QMT_CN_MIN_COMMISSION)
        rate = min(max(_configured_float(raw_rate, _QMT_CN_DEFAULT_COMMISSION_RATE), 0.0), _QMT_CN_MAX_COMMISSION_RATE)
        minimum = max(_configured_float(raw_min, _QMT_CN_MIN_COMMISSION), _QMT_CN_MIN_COMMISSION)
        return max(trade_value * rate, minimum)


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
        commission = broker._commission_from_trade_record(trade)
        if commission <= 0:
            commission = broker.estimate_commission(symbol, side, fill_qty, fill_price)
        broker._notify_trade_callbacks(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=fill_qty,
            price=fill_price,
            commission=commission,
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
