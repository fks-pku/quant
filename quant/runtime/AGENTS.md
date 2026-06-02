# Runtime Package

## 职责

跨回测和实盘复用的策略生命周期分发 helper。只负责调用策略 hook、校验日线快照完整性、解析执行参考价，不处理数据读取、订单执行、成交撮合或绩效计算。

## 对外契约

- `start_strategy(strategy)`
- `stop_strategy(strategy)`
- `before_trading(strategy, trading_date)`
- `after_trading(strategy, trading_date)`
- `feed_strategy_bars(strategy, bars)` — 优先调用 `on_data_batch(context, bars)`，否则逐条回落 `on_data(context, bar)`
- `build_daily_snapshot(bars, trading_date, required_symbols)` — 按 symbol 聚合同一交易日 bar，报告 missing/stale/duplicate
- `run_daily_snapshot(strategy, trading_date, bars, strict=True, call_before=False)` — 共享日线策略执行入口：完整快照 → 批量喂 bar → `after_trading`
- `ExecutionReferencePriceResolver(mode, broker, data_provider, allow_strategy_price_fallback=False)` — MARKET 执行参考价解析：优先 broker/data_provider quote open，再按 side/last 退化；默认不使用策略传入价兜底

## 依赖

- 仅依赖 stdlib 和 duck-typed strategy/context
- 不得依赖 `features/` 或 `infrastructure/`

## 不变量

- 回测和实盘都通过 `feed_strategy_bars()` 喂 bar，避免 batch 策略在不同模式下走不同入口
- Runtime helper 不保存跨事件状态，不生成订单，不改变策略调仓 gate
- 日线策略的完整性检查必须以策略 `symbols` 为 required symbols；strict 模式遇到缺失或跨日 bar 必须跳过策略 hook
- 实盘 MARKET 目标单有 resolver 时，策略传入 price 只能作为兼容输入，不能覆盖 broker/data-provider 的执行参考价

## 修改守则

- 改 lifecycle 分发语义时，必须同步更新 backtest/trading 相关测试
- 改 daily snapshot 语义时，必须覆盖完整快照、缺失 symbol、实盘事件流触发时点
- 改 execution reference 语义时，必须覆盖 broker quote 优先、缺 quote 不下单、策略 price fallback 开关
