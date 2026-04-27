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
- `risk_engine.reset_daily()` 在每个交易日结束时调用，重置订单计数器和 pending 追踪
- `portfolio.update_position()` SELL 路径通过 `realized_pnl` 参数跟踪已实现盈亏

## 修改守则

- 改引擎逻辑：只动 `engine.py`
- 改风控规则：只动 `risk.py`
- 改组合计算：只动 `portfolio.py`
- 改调度逻辑：只动 `scheduler.py`

## Known Pitfalls

- `Engine._on_data()` 使用 `hasattr(self.broker, 'update_price')` 多态调用，不使用 isinstance
- 策略通过 Context 访问所有系统组件，不要直接引用 Engine
- `RiskEngine._check_order_rate()` 回测模式（as_of_date != None）用 `_daily_order_count` 日计数器替代 wall clock，实盘模式仍用 `_order_timestamps` 时间戳列表
- `RiskEngine._check_position_size()` 累计 `_pending_order_values[symbol]` 防止同日多次下单绕过仓位限制，`record_order()` 负责更新此字典
- `Portfolio.update_position()` 的 `realized_pnl` 参数仅对 SELL（quantity < 0）路径生效，BUY 路径忽略该参数
