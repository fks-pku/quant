# QMT Broker Adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `QMTBroker` adapter implementing `BrokerAdapter` port, enabling A-share trading via 国金迅投 QMT (MiniQMT xtquant SDK).

**Architecture:** Single-file broker adapter in `infrastructure/execution/brokers/qmt.py`, wired into `QuantSystem._setup_broker()` via config-driven discovery. Follows `PaperBroker` single-file pattern (not Futu multi-mixin). CN symbol routing via `config.yaml broker_routing.CN = qmt`.

**Tech Stack:** Python 3.10+, `xtquant` SDK (from QMT client), `threading.RLock`, frozen dataclass pattern.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `quant/infrastructure/execution/brokers/qmt.py` | CREATE | QMTBroker: connect, submit, cancel, positions, account, callback |
| `quant/infrastructure/execution/brokers/__init__.py` | MODIFY | Export QMTBroker |
| `quant/shared/config/brokers.yaml` | MODIFY | Add qmt credential section |
| `quant/shared/config/config.yaml` | MODIFY | Add CN broker routing, comment qmt in brokers list |
| `quant/quant_system.py` | MODIFY | _setup_broker("qmt") branch |

---

### Task 1: Add QMT config to brokers.yaml

**Files:**
- Modify: `quant/shared/config/brokers.yaml`

- [ ] **Step 1: Append qmt section to brokers.yaml**

After the existing `futu:` block and before `ibkr:`, add:

```yaml
qmt:
  # MiniQMT (xtquant) - A-share trading
  host: "127.0.0.1"
  port: 58610
  # 资金账号，留空自动取默认账号
  account: ""
  # 交易密码（实盘必填，模拟盘留空）
  password: ""
  # 交易模式: SIMULATE (模拟盘) 或 REAL (实盘)
  trade_mode: SIMULATE
  # xtquant 库路径（非必填，默认从 QMT 安装目录自动发现）
  # 示例: "D:/国金证券QMT交易端/bin.x64/Lib/site-packages"
  mini_qmt_path: ""
```

- [ ] **Step 2: Commit**

```bash
git add quant/shared/config/brokers.yaml
git commit -m "config: add qmt broker section to brokers.yaml"
```

---

### Task 2: Update config.yaml for CN routing

**Files:**
- Modify: `quant/shared/config/config.yaml:40-44`

- [ ] **Step 1: Update execution.brokers and broker_routing**

Change lines 39-44 of config.yaml:

Old:
```yaml
execution:
  brokers:
    - paper
  broker_routing:
    US: paper
    HK: paper
```

New:
```yaml
execution:
  brokers:
    - paper
    # - qmt             # 取消注释以启用 QMT A股交易
  broker_routing:
    US: paper
    HK: paper
    CN: qmt              # A股路由到QMT
```

- [ ] **Step 2: Commit**

```bash
git add quant/shared/config/config.yaml
git commit -m "config: add CN market broker routing to qmt"
```

---

### Task 3: Implement QMTBroker

**Files:**
- Create: `quant/infrastructure/execution/brokers/qmt.py`

- [ ] **Step 1: Create qmt.py with full implementation**

```python
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path

from quant.domain.models.order import Order, OrderSide, OrderStatus, OrderType
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.ports.broker import BrokerAdapter
from quant.shared.utils.logger import setup_logger


_SZ_PREFIXES = ("000", "001", "002", "003", "300")
_BJ_PREFIXES = ("4", "8", "920")


def _cn_symbol_to_qmt(symbol: str) -> str:
    """Convert 6-digit CN symbol to QMT format: 600519 -> 600519.SH"""
    if symbol.startswith("HK.") or symbol.startswith("US."):
        return symbol
    code = symbol.replace(".", "")
    if code.startswith(_BJ_PREFIXES):
        return f"{code}.BJ"
    if code.startswith(_SZ_PREFIXES):
        return f"{code}.SZ"
    return f"{code}.SH"


def _qmt_symbol_to_cn(symbol: str) -> str:
    """Convert QMT format back to 6-digit CN symbol: 600519.SH -> 600519"""
    if "." in symbol:
        return symbol.split(".")[0]
    return symbol


class QMTBroker(BrokerAdapter):
    """QMT (MiniQMT) broker adapter for A-share trading via xtquant SDK."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 58610,
        account: str = "",
        trade_mode: str = "SIMULATE",
        password: str = "",
        mini_qmt_path: str = "",
    ):
        super().__init__("qmt")
        self._host = host
        self._port = port
        self._account = account
        self._trade_mode = trade_mode
        self._password = password
        self._mini_qmt_path = mini_qmt_path

        self._trader: Any = None
        self._session_id: int = 0
        self._pending_orders: Dict[str, Order] = {}
        self._positions_cache: Dict[str, Position] = {}
        self._account_info_cache: Optional[AccountInfo] = None
        self._lock = threading.RLock()
        self.logger = setup_logger("QMTBroker")

    def connect(self) -> None:
        if self._mini_qmt_path:
            _path = Path(self._mini_qmt_path)
            if str(_path) not in sys.path:
                sys.path.insert(0, str(_path))

        self._import_xtquant()

        try:
            from xtquant import xttrader
        except ImportError:
            raise ImportError(
                "xtquant not found. Install from QMT client directory: "
                "pip install <QMT_PATH>/bin.x64/Lib/site-packages/xtquant-*.whl"
                " or set mini_qmt_path in brokers.yaml"
            )

        self._session_id = int(datetime.now().timestamp() % 100000)

        _BaseCallback = xttrader.XtQuantTraderCallback
        broker_ref = self

        class _Callback(_BaseCallback):
            def __init__(inner_self):
                super().__init__()
                inner_self._broker = broker_ref
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

        callback = _Callback()
        self._trader = xttrader.XtQuantTrader(
            self._mini_qmt_path or "",
            self._session_id,
            callback,
        )
        self._trader.start()
        conn_result = self._trader.connect()
        if conn_result != 0:
            self._connected = False
            raise ConnectionError(
                f"QMT connection failed (code={conn_result}). "
                "Ensure QMT client is running and MiniQMT is enabled."
            )

        if not self._account:
            accounts = self._trader.query_accounts()
            if accounts and isinstance(accounts, list) and len(accounts) > 0:
                self._account = str(accounts[0])
                self.logger.info(f"Auto-detected account: {self._account}")
            else:
                raise RuntimeError("No QMT account found. Specify account in brokers.yaml")

        if self._trade_mode == "REAL" and self._password:
            self._unlock_trade()

        self._connected = True
        self._refresh_account()
        self._refresh_positions()
        self.logger.info(f"QMT broker connected (account={self._account}, mode={self._trade_mode})")

    def _import_xtquant(self) -> None:
        try:
            import xtquant
        except ImportError:
            if self._mini_qmt_path:
                raise ImportError(
                    f"xtquant not found at {self._mini_qmt_path}. "
                    "Verify the QMT client installation path."
                )
            raise ImportError(
                "xtquant not found. Set mini_qmt_path in brokers.yaml "
                "to the QMT installation directory."
            )

    def _unlock_trade(self) -> None:
        try:
            if hasattr(self._trader, 'unlock_trade'):
                self._trader.unlock_trade(self._password)
            else:
                self.logger.warning(
                    "QMT auto-unlock not available. "
                    "Please unlock trading in QMT client manually."
                )
            self.logger.info("QMT trading unlocked")
        except Exception as e:
            self.logger.warning(f"QMT unlock failed: {e}. Unlock manually in QMT client.")

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            if self._trader:
                try:
                    self._trader.stop()
                except Exception:
                    pass
                self._trader = None
            self.logger.info("QMT broker disconnected")

    def is_connected(self) -> bool:
        return self._connected

    def submit_order(self, order: Order) -> str:
        with self._lock:
            self._ensure_connected()

            qmt_code = _cn_symbol_to_qmt(order.symbol)

            order_type = _QMT_ORDER_TYPE_MAP.get(order.side, 23)
            price_type = 5 if order.order_type == OrderType.MARKET else 11
            price = 0.0 if order.order_type == OrderType.MARKET else (order.price or 0.0)
            volume = int(order.quantity)
            strategy_name = getattr(order, 'strategy_name', '') or 'quant'

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
                order.remark if hasattr(order, 'remark') else "",
            )

            if result is None or (isinstance(result, int) and result != 0):
                raise RuntimeError(
                    f"QMT order_stock failed for {order.symbol}: result={result}"
                )

            order_id = str(result)
            updated = self._set_order_attrs(order, {
                'order_id': order_id,
                'status': OrderStatus.SUBMITTED,
                'timestamp': datetime.now(),
            })
            self._pending_orders[order_id] = updated
            return order_id

    def cancel_order(self, order_id: str) -> bool:
        with self._lock:
            self._ensure_connected()
            result = self._trader.cancel_order_stock(self._account, order_id)
            if result is None or (isinstance(result, int) and result != 0):
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
                account_id=self._account,
                cash=0.0,
                buying_power=0.0,
                equity=0.0,
            )

    def _refresh_positions(self) -> None:
        if not self._connected or not self._trader:
            return
        try:
            df = self._trader.query_stock_positions(self._account)
            if df is None or (hasattr(df, 'empty') and df.empty):
                return
            self._positions_cache.clear()
            for _, row in df.iterrows():
                code = _qmt_symbol_to_cn(str(row.get('stock_code', '')))
                qty = float(row.get('volume', 0) or 0)
                if qty <= 0:
                    continue
                cost = float(row.get('cost_price', 0) or 0)
                mkt_val = float(row.get('market_value', 0) or 0)
                pnl = float(row.get('profit', 0) or 0)
                pos = Position(
                    symbol=code,
                    quantity=qty,
                    avg_cost=cost,
                    market_value=mkt_val,
                    unrealized_pnl=pnl,
                )
                self._positions_cache[code] = pos
        except Exception as e:
            self.logger.warning(f"QMT position query failed: {e}")

    def _refresh_account(self) -> None:
        if not self._connected or not self._trader:
            return
        try:
            asset = self._trader.query_stock_asset(self._account)
            if asset is None:
                return
            if hasattr(asset, 'iloc'):
                asset = asset.iloc[0]
            cash = float(getattr(asset, 'cash', 0) or 0)
            market_value = float(getattr(asset, 'market_value', 0) or 0)
            total_asset = float(getattr(asset, 'total_asset', 0) or 0)
            self._account_info_cache = AccountInfo(
                account_id=self._account,
                cash=cash,
                buying_power=cash,
                equity=total_asset,
                margin_used=0.0,
            )
        except Exception as e:
            self.logger.warning(f"QMT account query failed: {e}")

    def _ensure_connected(self) -> None:
        if not self._connected or not self._trader:
            raise RuntimeError("QMT broker not connected")

    def _set_order_attrs(self, order: Order, updates: dict) -> Order:
        from dataclasses import fields as dc_fields
        frozen = getattr(order, '__dataclass_params__', None)
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


_QMT_ORDER_TYPE_MAP = {
    OrderSide.BUY: 23,
    OrderSide.SELL: 24,
}


_ORDER_STATUS_MAP = {
    48: OrderStatus.PENDING,
    49: OrderStatus.PENDING,
    50: OrderStatus.SUBMITTED,
    51: OrderStatus.SUBMITTED,
    52: OrderStatus.PARTIAL,
    53: OrderStatus.PARTIAL,
    54: OrderStatus.CANCELLED,
    55: OrderStatus.PARTIAL,
    56: OrderStatus.FILLED,
    57: OrderStatus.REJECTED,
}


def _qmt_order_callback(broker: QMTBroker, order: Any) -> None:
    order_id = str(getattr(order, 'm_strOrderSysID', None) or getattr(order, 'order_id', ''))
    qmt_status = getattr(order, 'm_nOrderStatus', None)
    if qmt_status is None:
        qmt_status = getattr(order, 'order_status', 0)
    mapped = _ORDER_STATUS_MAP.get(qmt_status, OrderStatus.SUBMITTED)
    with broker._lock:
        if order_id in broker._pending_orders:
            existing = broker._pending_orders[order_id]
            traded_vol = getattr(order, 'm_nVolumeTraded', None)
            if traded_vol is None:
                traded_vol = getattr(order, 'traded_volume', 0)
            broker._pending_orders[order_id] = broker._set_order_attrs(
                existing,
                {'status': mapped, 'filled_quantity': float(traded_vol or 0)},
            )


def _qmt_trade_callback(broker: QMTBroker, trade: Any) -> None:
    order_id = str(getattr(trade, 'm_strOrderSysID', None) or getattr(trade, 'order_id', ''))
    fill_price = float(getattr(trade, 'm_dPrice', None) or getattr(trade, 'traded_price', 0) or 0)
    fill_qty = float(getattr(trade, 'm_nVolume', None) or getattr(trade, 'traded_volume', 0) or 0)
    with broker._lock:
        if order_id in broker._pending_orders:
            existing = broker._pending_orders[order_id]
            filled_qty = existing.filled_quantity + fill_qty
            avg_price = fill_price
            if existing.filled_quantity > 0 and existing.avg_fill_price:
                avg_price = (
                    (existing.avg_fill_price * existing.filled_quantity + fill_price * fill_qty)
                    / filled_qty
                )
            broker._pending_orders[order_id] = broker._set_order_attrs(
                existing,
                {'filled_quantity': filled_qty, 'avg_fill_price': avg_price},
            )
    broker.logger.info(f"QMT trade: {fill_qty} @ {fill_price}, order={order_id}")


def _qmt_order_error_callback(broker: QMTBroker, err: Any) -> None:
    order_id = str(getattr(err, 'm_strOrderSysID', None) or getattr(err, 'order_id', ''))
    error_msg = str(getattr(err, 'm_strErrorInfo', None) or getattr(err, 'error_info', ''))
    with broker._lock:
        if order_id and order_id in broker._pending_orders:
            existing = broker._pending_orders[order_id]
            broker._pending_orders[order_id] = broker._set_order_attrs(
                existing,
                {'status': OrderStatus.REJECTED},
            )
    broker.logger.error(f"QMT order error: id={order_id}, msg={error_msg}")


def _qmt_cancel_error_callback(broker: QMTBroker, err: Any) -> None:
    broker.logger.error(f"QMT cancel error: {err}")


def _qmt_asset_callback(broker: QMTBroker, asset: Any) -> None:
    with broker._lock:
        broker._account_info_cache = AccountInfo(
            account_id=broker._account,
            cash=float(getattr(asset, 'm_dAvailable', None) or 0),
            buying_power=float(getattr(asset, 'm_dAvailable', None) or 0),
            equity=float(getattr(asset, 'm_dBalance', None) or 0),
            margin_used=0.0,
        )


def _qmt_position_callback(broker: QMTBroker, position: Any) -> None:
    code = _qmt_symbol_to_cn(str(getattr(position, 'm_strInstrumentID', None) or ''))
    qty = float(getattr(position, 'm_nVolume', None) or 0)
    if qty <= 0:
        return
    with broker._lock:
        broker._positions_cache[code] = Position(
            symbol=code,
            quantity=qty,
            avg_cost=float(getattr(position, 'm_dOpenPrice', None) or 0),
            market_value=float(getattr(position, 'm_dMarketValue', None) or 0),
            unrealized_pnl=float(getattr(position, 'm_dFloatProfit', None) or 0),
        )
```

- [ ] **Step 2: Commit**

```bash
git add quant/infrastructure/execution/brokers/qmt.py
git commit -m "feat: implement QMTBroker adapter for A-share trading via xtquant"
```

---

### Task 4: Export QMTBroker from __init__.py

**Files:**
- Modify: `quant/infrastructure/execution/brokers/__init__.py`

- [ ] **Step 1: Add QMTBroker import and export**

Old:
```python
"""Broker adapters package."""

from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.infrastructure.execution.brokers.paper import PaperBroker

__all__ = ["BrokerAdapter", "PaperBroker"]
```

New:
```python
"""Broker adapters package."""

from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.execution.brokers.qmt import QMTBroker

__all__ = ["BrokerAdapter", "PaperBroker", "QMTBroker"]
```

- [ ] **Step 2: Commit**

```bash
git add quant/infrastructure/execution/brokers/__init__.py
git commit -m "feat: export QMTBroker from brokers package"
```

---

### Task 5: Wire QMT broker into QuantSystem

**Files:**
- Modify: `quant/quant_system.py:108-134`

- [ ] **Step 1: Add qmt branch in _setup_broker**

After the `elif broker_name == "futu":` block (line 134), add:

```python
        elif broker_name == "qmt":
            host = self.config_loader.get("brokers.yaml", "qmt", "host", default="127.0.0.1")
            port = self.config_loader.get("brokers.yaml", "qmt", "port", default=58610)
            account = self.config_loader.get("brokers.yaml", "qmt", "account", default="")
            password = self.config_loader.get("brokers.yaml", "qmt", "password", default="")
            trade_mode = self.config_loader.get("brokers.yaml", "qmt", "trade_mode", default="SIMULATE")
            mini_qmt_path = self.config_loader.get("brokers.yaml", "qmt", "mini_qmt_path", default="")
            from quant.infrastructure.execution.brokers.qmt import QMTBroker
            broker = QMTBroker(
                host=host,
                port=port,
                account=account,
                password=password,
                trade_mode=trade_mode,
                mini_qmt_path=mini_qmt_path,
            )
            broker.connect()
            self.engine.set_broker(broker)
            self.logger.info(f"QMT broker initialized (mode: {trade_mode})")
```

Also update `_setup_order_manager` to register qmt broker:

Old (line 144-146):
```python
        if self.engine.broker:
            order_manager.register_broker("paper", self.engine.broker)
            order_manager.register_broker("futu", self.engine.broker)
```

New:
```python
        if self.engine.broker:
            broker_name = getattr(self.engine.broker, '_name', 'paper')
            order_manager.register_broker(broker_name, self.engine.broker)
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
python -m pytest quant/tests/ -q --ignore=quant/tests/test_backtest_invariants.py --ignore=quant/tests/test_strategies_invariants.py -x
```

Expected: all tests pass (no runtime import of QMTBroker until `"qmt"` is in the broker list).

- [ ] **Step 3: Commit**

```bash
git add quant/quant_system.py
git commit -m "feat: wire QMT broker into QuantSystem startup"
```

---

### Task 6: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest quant/tests/ -q
```

Expected: all tests pass. No regressions from adding new broker adapter.

- [ ] **Step 2: Verify QMTBroker import works standalone**

```bash
python -c "from quant.infrastructure.execution.brokers.qmt import QMTBroker; print('Import OK')"
```

Expected: `Import OK` (QMTBroker import succeeds even without xtquant installed, since xtquant is imported lazily in `connect()`).

- [ ] **Step 3: Commit if any changes from test fixes**

```bash
git add -A
git commit -m "test: verify QMTBroker integration passes test suite"
```
