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
- 实盘策略分账时，OrderManager/FillHandler 必须通过 resolver 使用所属策略的 RiskEngine/SubPortfolio
- 带 `strategy_name` 的实盘成交只能派发给所属策略，不能广播到其他策略
- Live `MARKET + price` 信号单经 `LiveExecutionManager` 转为限成本 LIMIT；显式 LIMIT 保持原限价；到期目标单撤单并丢弃
- 默认 `live_trading.daily_snapshot_mode=True`：BAR 事件只进入日线快照缓存；`MARKET_CLOSE` 标记快照完成；下一次 `MARKET_OPEN` 用最新完整交易日快照触发策略，缺失 required symbol 时跳过
- Live/Paper `MARKET` 目标单必须通过 `ExecutionReferencePriceResolver` 取 broker/data-provider 执行参考价；默认不得用策略传入 price 兜底

## 修改守则

- 改引擎逻辑：只动 `engine.py`
- 改风控规则：只动 `risk.py`
- 改组合计算：只动 `portfolio.py`
- 改调度逻辑：只动 `scheduler.py`
- 改实盘下单归因、成本限价、成交记录：优先动 `infrastructure/execution/` 和 `quant_system.py` 组合根，不要让 `features/` 直接依赖 `infrastructure`

## Known Pitfalls

- `Engine._on_data()` 使用 `hasattr(self.broker, 'update_price')` 多态调用，不使用 isinstance
- 策略通过 Context 访问所有系统组件，不要直接引用 Engine
- `RiskEngine._check_order_rate()` 回测模式（as_of_date != None）用 `_daily_order_count` 日计数器替代 wall clock，实盘模式仍用 `_order_timestamps` 时间戳列表
- Backtest order-rate limiting is disabled by default; set `risk.max_orders_per_day` or explicit `risk.max_orders_minute` when a daily order cap is intended.
- `RiskEngine._check_position_size()` 累计 `_pending_order_values[symbol]` 防止同日多次下单绕过仓位限制，`record_order()` 负责更新此字典
- `Portfolio.update_position()` 的 `realized_pnl` 参数仅对 SELL（quantity < 0）路径生效，BUY 路径忽略该参数
- `Context.submit_order()` 把 `MARKET` 订单交给 execution_manager；若存在 execution reference resolver，参考价必须来自 resolver；显式 LIMIT 单必须直接传给 OrderManager
- 日线策略需要逐 BAR 实时回调时必须显式设置 `live_trading.feed_intraday_bars=True`；否则默认只在次日开盘处理上一交易日完整快照
- 实盘中断恢复时，日线快照模式只消费最新完成快照，不补发更早交易日的过期信号
- `live_trading.execution_reference.allow_strategy_price_fallback=True` 只用于迁移或测试；实盘默认应保持 False
