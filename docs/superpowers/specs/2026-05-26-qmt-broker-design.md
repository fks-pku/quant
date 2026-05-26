# QMT Broker Adapter — Design Spec

## Purpose

为 A 股实盘/模拟交易接入国金迅投 QMT（MiniQMT 模式），实现 `BrokerAdapter` 端口，支持下单、撤单、持仓查询、账户查询。

## Architecture

遵循 Hexagonal Architecture：`infrastructure/execution/brokers/qmt.py` 实现 `domain/ports/BrokerAdapter`。

```
quant/
├── infrastructure/execution/brokers/qmt.py   # NEW: QMTBroker
├── shared/config/brokers.yaml                 # EDIT: +qmt section
├── shared/config/config.yaml                  # EDIT: execution.brokers + broker_routing.CN
└── quant_system.py                            # EDIT: _setup_broker() +qmt branch
```

## QMTBroker Contract

### Constructor
```python
QMTBroker(host="127.0.0.1", port=58610, account="",
          trade_mode="SIMULATE", password="", mini_qmt_path="")
```

### BrokerAdapter Methods

| Method | QMT API Mapping |
|---|---|
| `connect()` | XtQuantTrader(host, port), register callback, start |
| `disconnect()` | xttrader.stop(), cleanup |
| `is_connected()` | connection state flag |
| `submit_order(order)` | xttrader.order_stock(account, code, type, volume, price_type, price, strategy, remark) |
| `cancel_order(order_id)` | xttrader.cancel_order_stock(account, order_id) |
| `get_order_status(order_id)` | local _pending_orders cache (updated via callback) |
| `get_positions()` | xttrader.query_stock_positions(account) -> List[Position] |
| `get_account_info()` | xttrader.query_stock_asset(account) -> AccountInfo |

### Callback Handling

继承 `XtQuantTraderCallback`，在 `on_stock_order()` / `on_stock_trade()` 中同步本地状态。

### Thread Safety

`_pending_orders` / `_positions` 使用 `threading.RLock()` 保护。

### Trade Mode

- `SIMULATE`: 无需密码，连接 QMT 模拟柜台
- `REAL`: 需交易密码，调用 `unlock_trade(password, "REAL")`（如 API 不支持则提示用户在 QMT 客户端手动解锁）

### Error Handling

QMT API 调用失败抛异常，上层 `OrderManager` 已具备 3 次指数退避重试。

## Config Changes

### brokers.yaml — add qmt section
```yaml
qmt:
  host: "127.0.0.1"
  port: 58610
  account: ""
  password: ""
  trade_mode: SIMULATE
  mini_qmt_path: ""
```

### config.yaml — execution section
```yaml
execution:
  brokers:
    - paper
    # - qmt          # uncomment to enable
  broker_routing:
    US: paper
    HK: paper
    CN: qmt           # A-share -> QMT
```

## QuantSystem Integration

`_setup_broker("qmt")` 分支：
1. 从 `brokers.yaml` 读 qmt 配置
2. 创建 `QMTBroker(host, port, account, password, trade_mode)`
3. 调用 `.connect()`
4. 若 `trade_mode == "REAL"` 且有 password，调 `.unlock_trade()`
5. `engine.set_broker(broker)`
6. `order_manager.register_broker("qmt", broker, symbols)`

## Files Changed

| File | Change |
|---|---|
| `quant/infrastructure/execution/brokers/qmt.py` | NEW — QMTBroker implementation |
| `quant/shared/config/brokers.yaml` | +qmt config section |
| `quant/shared/config/config.yaml` | broker_routing.CN |
| `quant/quant_system.py` | _setup_broker +qmt branch |

## Not in Scope

- QMT 行情源（xtdata DataFeed）— 继续使用现有 Tushare 行情
- 可转债/期权 — 仅 A 股正股 + ETF/LOF
- 多账户管理 — 单资金账号
