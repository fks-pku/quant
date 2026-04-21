# Trading Feature

## 职责

实盘/模拟交易的全生命周期管理。包含交易引擎、风控、调度器、组合管理。

## 对外契约

- `Engine(config)` - 交易引擎主类
- `SystemMode(Enum)` - LIVE/PAPER/BACKTEST
- `Context` - 策略上下文
- `Portfolio` - 组合跟踪
- `RiskEngine` - 风控引擎
- `Scheduler` - 市场时间调度

## 依赖

- `infrastructure/events` - EventBus
- `infrastructure/execution` - OrderManager, FillHandler
- `shared/models` - Order, Position
- `shared/utils` - logger, datetime_utils

## 不变量

- Engine 在单独线程运行，通过 EventBus 与策略通信
- Portfolio 是线程安全的（使用 RLock）
- RiskEngine 在订单提交前进行风控检查

## 修改守则

- 改引擎逻辑：只动 `engine.py`
- 改风控规则：只动 `risk.py`
- 改组合计算：只动 `portfolio.py`
- 改调度逻辑：只动 `scheduler.py`

## Known Pitfalls

- `Engine._on_data()` 使用 `hasattr(self.broker, 'update_price')` 多态调用，不使用 isinstance
- 策略通过 Context 访问所有系统组件，不要直接引用 Engine
