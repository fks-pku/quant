# Runtime Package

Current daily target execution-cost contract: D-day strategy price or signal-bar close is used only to estimate the acceptable `execution_cost_bps`; the executable live/paper `LIMIT` price is computed later from execution-day open/reference price plus or minus that bps budget. Pending-only generation records the bps budget and must not persist a D-close-derived limit price.

## 职责

跨回测和实盘复用的策略生命周期分发 helper。只负责调用策略 hook、校验日线快照完整性、解析执行参考价、提供纯成本估算函数，不处理数据读取、订单执行、成交撮合或绩效计算。

## 对外契约

- `start_strategy(strategy)`
- `stop_strategy(strategy)`
- `before_trading(strategy, trading_date)`
- `after_trading(strategy, trading_date)`
- `feed_strategy_bars(strategy, bars)` — 优先调用 `on_data_batch(context, bars)`，否则逐条回落 `on_data(context, bar)`
- `build_daily_snapshot(bars, trading_date, required_symbols)` — 按 symbol 聚合同一交易日 bar，报告 missing/stale/duplicate
- `run_daily_snapshot(strategy, trading_date, bars, strict=True, call_before=False, after_feed=None)` — 单策略日线执行入口，内部复用批量 runner
- `run_daily_snapshots(strategies, trading_date, bars, strict=True, call_before=False, after_feed=None)` — 共享日线策略执行入口：完整快照校验 → 全部 runnable 策略批量喂 bar → `after_feed` → 全部 runnable 策略 `after_trading`
- `ExecutionReferencePriceResolver(mode, broker, data_provider, allow_strategy_price_fallback=False)` — MARKET 执行参考价解析：优先 broker/data_provider quote open，再按 side/last 退化；默认不使用策略传入价兜底
- `estimate_cost_protection_limit(...)` — 纯函数：用 D 日可知 reference/bar 和 `execution_cost_model` 估算成本保护 bps，不读未来 bar、不提交订单；返回的 `limit_price` 只可作诊断，订单路径必须用执行日 open/reference 重新锚定真实 LIMIT

## 依赖

- 仅依赖 stdlib 和 duck-typed strategy/context
- 不得依赖 `features/` 或 `infrastructure/`

## 不变量

- 回测和实盘都通过 `feed_strategy_bars()` 喂 bar，避免 batch 策略在不同模式下走不同入口
- 回测、模拟盘、实盘的收盘信号触发必须通过 `run_daily_snapshots()`；只允许 D+1 下单价格、撮合、成交结果按模式分化
- Runtime helper 不保存跨事件状态，不生成订单，不改变策略调仓 gate
- 日线策略的完整性检查必须以策略 `symbols` 为 required symbols；strict 模式遇到缺失或跨日 bar 必须跳过策略 hook
- Daily `MARKET` target generation must separate D-day cost bps estimation from execution-day LIMIT anchoring: strategy price/signal close is the cost reference, while resolver open/reference is the D+1 execution anchor.

## 修改守则

- 改 lifecycle 分发语义时，必须同步更新 backtest/trading 相关测试
- 改 daily snapshot 语义时，必须覆盖完整快照、缺失 symbol、实盘事件流触发时点
- 改 execution reference 语义时，必须覆盖 D 日信号价优先、signal bar close 兜底、resolver 仅作执行日 anchor、缺 D 日 reference 或缺执行 anchor 不下单
- 改 execution cost 语义时，必须覆盖 D-day cost bps 与 execution-day open/reference LIMIT anchoring 分离。
