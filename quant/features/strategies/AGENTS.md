# Strategies Feature

## 职责

策略定义、注册、发现、框架组件。包含策略基类、日线策略辅助基类、注册表，以及研究生成/候选策略目录。

## 对外契约

- `Strategy(ABC)` - 策略抽象基类
- `Strategy.on_data_batch(context, data)` - 日线批量喂数 hook，默认逐条回落到 `on_data`
- `StrategyRegistry` - 策略注册表
- `@strategy` - 策略装饰器
- `_large_cap_forum_common.py` - large-cap forum candidate strategies share this daily composite-factor base; each reportable thesis still has its own candidate directory and registry id

## 依赖

- `shared/utils` - logger
- `infrastructure/events` - EventBus (通过 Context)

## 不变量

- 策略层不变量详见 `quant/features/strategies/docs/strategy-invariants.md`；成交、现金、费用、T+1、NAV 等执行语义属于 `quant/features/backtest/docs/backtest-invariants.md`
- 所有策略必须使用 `@strategy` 装饰器注册
- 所有策略的买入执行都必须接受框架层 ADV 约束：最终 BUY 成交金额不得超过配置的 `max_participation_rate * ADV value`，研究默认生产门槛为 5% ADV；策略不得用自身下单逻辑绕过该约束
- 策略 ID 唯一，不区分大小写
- 策略目录结构: `strategies/<name>/strategy.py`
- 顶层策略准入门槛：只有当前生产 checklist 全部通过的策略目录才允许直接放在 `strategies/<name>/`，并且目录内必须附带与研究产物同步的 `full_research_report.html`；其余候选必须放在 `strategies/reject/<name>/`
- 研究生成策略默认保持 `enabled: false` / `status: candidate`
- A 股低价小市值类策略必须显式处理退市风险：买入过滤 ST/停牌/非上市/list_status、价格下限和流动性下限，持仓触发风险时每日尝试退出
- 日线策略的持仓风险退出必须独立于 `holding_days`/调仓门控；风险退出在每日 `on_after_trading` 先执行，且同日调仓不得对已提交风险退出的 symbol 再次提交 SELL
- risk-off 清仓后必须重置调仓 gate；RSRS/风控等外层 regime 从 off 重新切到 on 时，不能被旧的 `_last_rebalance_date` 卡住
- 只有实际完成候选选择/调仓的 rebalance 才能刷新 `_last_rebalance_date`；空候选池、字段缺失或无有效 NAV 不得让策略等待下一个 holding window
- 候选入场过滤与持仓退出过滤必须分离；入场 diagnostics 不得混入 `stop_loss`、`take_profit`、`trailing_stop` 等只对已有持仓有意义的退出原因
- 覆盖 `on_fill()` 的策略必须兼容回测引擎的 synthetic fill：送股/转增可能以 `BUY`、`fill_price=0` 回调同步策略内部仓位，ETF/基金 `adj_factor` 份额折算可能以 `BUY` 或 `SELL`、`fill_price=0` 同步内部仓位，内部成本/峰值价状态要随之按数量比例调整
- 顶层 promoted/candidate 策略默认必须暴露并启用 `risk_exit.enabled` 或等价风险退出包；正式研究报告默认只展示启用后的止盈止损/风险退出逻辑，关闭版本只作为专项敏感性/消融研究
- 面向普通账户的 A 股个股策略默认必须排除需要额外权限的创业板 `300/301` 与科创板 `688/689` 股票；ETF 策略不适用该股票权限过滤

## 修改守则

- 改策略框架：只动 `base.py`, `registry.py`
- 新增策略：在 `strategies/` 下新建目录，包含 `strategy.py`、`config.yaml`、`README.md`
- 新增未验证策略或当前生产 checklist 未全部通过的策略：放入 `strategies/reject/<name>/`，不得进入顶层自动发现区；通过严格回测 checklist 后再 promote，并同步复制对应 `full_research_report.html` 到策略目录
- Large-cap forum strategies should reuse `_large_cap_forum_common.py` for shared daily-bar factor/risk mechanics, while preserving separate candidate directories, configs, and README files for each thesis.
- 非小市值 ETF 轮动策略应通过资产类别与流动性定义 universe；除非用户明确要求，不要把中证1000或其它小盘 proxy ETF 混入默认池。
- 黄金-权益 ETF 杠铃类策略可以使用黄金 ETF 作为防守资产，但权益腿只能来自已审计稳定 ETF 注册池，不能从当前全市场 ETF taxonomy 自动扩候选，也不能退化为单只股票或小盘 proxy；新增 ETF 类别必须先经用户审计同意并注册到 `audited_stable_etf_registry_v1`。每个调仓点仍必须满足当前 bar、NAV/规模、流动性和 lookback 约束。
- 只有当策略有额外本地约束或复杂审计要求时才新增策略目录级 `AGENTS.md`
- 新增或修复策略框架/日线策略状态机语义时，必须在 `quant/tests/test_strategies_invariants.py` 增加对应 CASE，并同步 `quant/features/strategies/docs/strategy-invariants.md`；单策略测试只能作为补充

## Known Pitfalls

- 策略在 `__init__` 中不要访问 Context，Context 在 `on_start` 时才设置
- 策略名称要与装饰器参数一致
- 面向大 universe 的日线策略应实现/继承批量 `on_data_batch`，不要在 engine 外重新按 symbol 点查 provider
- 调仓信号用收盘数据时订单是 T+1 执行，策略内部 `_positions` 会在次日 fill 后才变化；同一天的退出集合必须显式传递给 rebalance 逻辑，不能假设 `_positions` 已更新
- 使用 PnL 止盈止损时优先读取组合真实 `avg_cost`；策略内部 entry price 只是 fallback，遇到分红、送股、部分成交时容易陈旧
- 送股/拆股后策略内部 `_positions` 可能出现小于 1 股的残留；风险退出和清仓逻辑必须过滤零股卖单，不能提交 `SELL 0`
- 需要避免“信号稀疏时总仓位集中到单票”的多标的策略，应使用 `target_weight_slots` 按目标槽位分配仓位；不要用当前选中数量作为唯一分母来满足“不单吊”的风险约束
