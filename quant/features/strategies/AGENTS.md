# Strategies Feature

## 职责

策略定义、注册、发现、框架组件。包含策略基类、日线策略辅助基类、注册表，以及研究生成/候选策略目录。

## 对外契约

- `Strategy(ABC)` - 策略抽象基类
- `Strategy.on_data_batch(context, data)` - 日线批量喂数 hook，默认逐条回落到 `on_data`
- `StrategyRegistry` - 策略注册表
- `@strategy` - 策略装饰器

## 依赖

- `shared/utils` - logger
- `infrastructure/events` - EventBus (通过 Context)

## 不变量

- 所有策略必须使用 `@strategy` 装饰器注册
- 策略 ID 唯一，不区分大小写
- 策略目录结构: `strategies/<name>/strategy.py`
- 研究生成策略默认保持 `enabled: false` / `status: candidate`
- A 股低价小市值类策略必须显式处理退市风险：买入过滤 ST/停牌/非上市/list_status、价格下限和流动性下限，持仓触发风险时每日尝试退出
- 日线策略的持仓风险退出必须独立于 `holding_days`/调仓门控；风险退出在每日 `on_after_trading` 先执行，且同日调仓不得对已提交风险退出的 symbol 再次提交 SELL
- risk-off 清仓后必须重置调仓 gate；RSRS/风控等外层 regime 从 off 重新切到 on 时，不能被旧的 `_last_rebalance_date` 卡住
- 只有实际完成候选选择/调仓的 rebalance 才能刷新 `_last_rebalance_date`；空候选池、字段缺失或无有效 NAV 不得让策略等待下一个 holding window
- 候选入场过滤与持仓退出过滤必须分离；入场 diagnostics 不得混入 `stop_loss`、`take_profit`、`trailing_stop` 等只对已有持仓有意义的退出原因
- 覆盖 `on_fill()` 的策略必须兼容回测引擎的 synthetic fill：送股/转增可能以 `BUY`、`fill_price=0` 回调同步策略内部仓位，内部成本/峰值价状态要随之按数量比例调整

## 修改守则

- 改策略框架：只动 `base.py`, `registry.py`
- 新增策略：在 `strategies/` 下新建目录，包含 `strategy.py`、`config.yaml`、`README.md`
- 只有当策略有额外本地约束或复杂审计要求时才新增策略目录级 `AGENTS.md`
- 新增或修复日线策略状态机语义时，必须在 `quant/tests/test_strategies_invariants.py` 增加对应 CASE；单策略测试只能作为补充

## Known Pitfalls

- 策略在 `__init__` 中不要访问 Context，Context 在 `on_start` 时才设置
- 策略名称要与装饰器参数一致
- 面向大 universe 的日线策略应实现/继承批量 `on_data_batch`，不要在 engine 外重新按 symbol 点查 provider
- 调仓信号用收盘数据时订单是 T+1 执行，策略内部 `_positions` 会在次日 fill 后才变化；同一天的退出集合必须显式传递给 rebalance 逻辑，不能假设 `_positions` 已更新
- 使用 PnL 止盈止损时优先读取组合真实 `avg_cost`；策略内部 entry price 只是 fallback，遇到分红、送股、部分成交时容易陈旧
