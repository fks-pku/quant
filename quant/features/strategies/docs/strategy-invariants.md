# Strategy Invariants

策略层不变量描述订单发出之前必须成立的契约：策略生命周期、内部状态、信号价格和下单价格的分离、以及日线调仓状态机。成交、现金、手续费、滑点、涨跌停、T+1、NAV 等订单执行之后的语义属于回测层，维护在 `quant/features/backtest/docs/backtest-invariants.md`。

## 分层边界

| 类型 | 归属 | 测试 | 文档 |
|------|------|------|------|
| 订单提交前的策略状态、信号生成、调仓 gate、内部持仓镜像 | strategies | `quant/tests/test_strategies_invariants.py` | 本文档 |
| 订单提交后的撮合、拒单、费用、市场规则、权益计算 | backtest | `quant/tests/test_backtest_invariants.py` | `quant/features/backtest/docs/backtest-invariants.md` |
| 单个策略的参数、排序、阈值、特殊业务回归 | 对应策略测试 | `quant/tests/test_<strategy>.py` | 策略 README 或研究报告 |

如果一个单策略 bug 反映出所有策略或一类日线调仓策略都应该遵守的规则，修复时必须把它提升为本文档的 CASE，并在 `test_strategies_invariants.py` 加对应测试。单策略回归测试只能作为补充。

## CASE-1: Registry CRUD

验证策略注册表的基本契约。

### 断言

```
S1-01  @strategy("X") 注册后 StrategyRegistry.is_registered("X") 为 True
S1-02  StrategyRegistry.create("X", **kwargs) 返回目标策略实例并传入参数
S1-03  创建未知策略抛出 ValueError("Unknown strategy")
S1-04  StrategyRegistry.list_strategies() 包含已注册策略
S1-05  StrategyRegistry.register("X", cls) 必须补齐 cls._registry_name，避免手工注册类破坏 list/create 错误路径
```

## CASE-2: _adj helper priority

`Strategy._adj()` 用于信号和指标计算，优先返回复权价格，保持 A 股前复权/后复权序列的连续性。

### 断言

```
S2-01  bar 同时存在 close 和 adj_close 时，_adj(bar, "close") 返回 adj_close
S2-02  缺少 adj_close 时回落到 close
S2-03  adj_close 为 NaN 时回落到 close
S2-04  adj_close 为 None 时回落到 close
S2-05  CN adj_close = close * adj_factor 时，_adj 保留复权价格用于信号连续性
```

## CASE-2b: _price helper for order sizing

`Strategy._price()` 用于下单数量和资金占用估算，必须返回真实市场价格，不能使用复权价格。

### 断言

```
S2B-01  bar 同时存在 close 和 adj_close 时，_price(bar) 返回 close
S2B-02  A 股复权价很大时，_price 仍返回真实 close
S2B-03  close 为 0 时返回 0
S2B-04  缺少 close 时返回 0
S2B-05  object bar 也按 close 属性取真实价格
```

## CASE-3: buy/sell no-context silent failure

策略未绑定 Context 时，`buy()` / `sell()` 不应抛异常，也不应伪造订单。

### 断言

```
S3-01  未 on_start 的策略调用 buy(...) 返回 None
S3-02  未 on_start 的策略调用 sell(...) 返回 None
```

## CASE-4: on_fill updates internal positions

默认 `Strategy.on_fill()` 必须维护策略内部 `_positions` 镜像，让后续信号能看到成交后的仓位。

### 断言

```
S4-01  BUY fill 增加内部持仓数量
S4-02  SELL fill 减少内部持仓数量
```

## CASE-5: Daily risk-exit/rebalance state machine

日线调仓策略族通常在 `on_after_trading()` 同时处理风险退出、regime gate 和再平衡。该 CASE 约束的是这一类策略的通用状态机，不是某个单策略的特权规则。当前测试使用 `JoinquantValueRsrsTimingStrategy` 作为可执行样例，因为它覆盖了风险退出、RSRS regime、候选过滤和 synthetic fill。

### 断言

```
S5-01  当日已因风险退出提交 SELL 的 symbol，同日 rebalance 不得再次提交 SELL
S5-02  risk-off 清仓后，regime 从 off 切回 on 时必须绕过旧 rebalance gate 立即允许再入场
S5-03  空候选池、字段缺失或无有效 NAV 不得刷新 _last_rebalance_date / holding gate
S5-04  候选入场过滤不得使用 stop_loss / take_profit / trailing_stop 等持仓退出条件
S5-05  送股/转增 synthetic BUY fill_price=0 时，策略内部数量、entry price、峰值价必须按数量比例同步
S5-06  送股/拆分等事件产生小于 1 股的内部残留仓位时，风险退出不得提交 SELL 0；共享日线调仓基类也必须记录 dust_position 并等待组合/引擎清理
```

### 适用范围

- 适用于有 `holding_days`、定期调仓、风险退出、择时 gate 或候选池筛选的日线策略。
- 单纯 buy-and-hold 或一次性信号策略不一定需要实现所有 gate，但不能违反已适用的生命周期契约。
- PnL 型止盈止损属于已有持仓退出条件；允许读取真实 `avg_cost` 或策略内部 fill state，但不得参与候选入场过滤。
- 如果未来抽象出公共日线调仓基类，应把本 CASE 的状态机迁移到公共基类测试，并保留策略级回归测试验证接入正确。

## CASE-6: Top-level strategy promotion gate

顶层 `quant/features/strategies/<strategy_id>/` 是生产注册表自动发现区，不再存放所有研究候选。只有严格本地回测报告证明当前生产 checklist 全部通过的策略，才能留在顶层；目录内还必须携带与研究报告目录同步的 `full_research_report.html`，方便策略代码与完整审计证据一起归档。尚未最终 No-Go 的候选留在 `quant/features/strategies/reject/<strategy_id>/`；完整研究最终 rejected 的生成策略迁出 `strategies/`，归档到 `quant/features/rejected_strategy/<strategy_id>/`，只能通过显式归档路径或 loader 做审计/复盘。

### 断言

```
S6-01  `strategies/reject/` 目录存在，且顶层每个含 strategy.py 的策略目录都必须通过当前生产 checklist：CAGR/MaxDD 分层门槛、total_trades > 50、max_adv_participation <= 5% ADV
S6-02  顶层策略目录必须附带与 `quant/infrastructure/var/research/reports/<strategy_id>/full_research_report.html` 同步的 `full_research_report.html`
S6-03  测试和审计脚本不得通过缺失的顶层 `quant.features.strategies.<strategy_id>` 路径加载最终 rejected 归档策略；必须使用 `quant.features.rejected_strategy...` 或显式 archive loader
S6-04  已 promoted 的顶层策略入口不得回退导入同名 `features/rejected_strategy/<strategy_id>` 归档代码
S6-05  显式导入 `features/rejected_strategy/` 或 `strategies/reject/` 下的 `@strategy` 类，不得新增 active registry id，也不得覆盖同名 promoted 策略类
```

## CASE-7: Audited ETF registry universe

ETF 类别策略不能再用当前全市场基金分类自动回溯历史候选。代表性 ETF 类别必须先进入用户审计同意的注册表；策略运行时只能在注册 symbol 中按当日可见 bar、NAV/规模、流动性和 lookback 过滤，再由信号选择持仓。

### 断言

```
S7-01  注册类别中如果存在多个已审计候选，调仓日可见候选必须按策略信号排序，不得先按规模预选单个主代表再排序
S7-02  未来新发或当日无 current bar 的 symbol 即使在窗口 superset 中，也不得进入该调仓日的可选候选
S7-03  ETF 类别注册表中的每个 category entry 必须标记为 user_approved；新增类别必须通过注册表和测试审计
```

## CASE-8: Promoted strategy risk-exit toggle

顶层 promoted/candidate 策略默认必须带启用的止盈止损/风险退出包。开关能力保留给专项敏感性/消融研究使用，但默认 full report 不再展示关闭/开启对照；报告只解释当前启用口径下的退出逻辑和风险含义。

### 断言

```
S8-01  顶层策略默认 `risk_exit.enabled=True`，并在 `get_state()["parameters"]` 中暴露该配置
S8-02  `risk_exit.enabled=False` 时，PnL 型 stop_loss / trailing_take_profit / time_stop 不得触发；非 PnL 的上市状态、停牌、退市、低流动性护栏可继续独立执行
```

### 适用范围

- 适用于顶层 `quant/features/strategies/<strategy_id>/` 中可直接被研究/回测报告调用的策略。
- 若策略确实不适合单票级止盈止损，必须实现组合级回撤、波动率、时间退出或在报告中明确标记不适用，并说明默认启用的风险退出口径。

## CASE-9: Retail-permission stock universe

面向普通账户的 A 股个股策略默认不得买入需要额外权限的创业板和科创板股票。该约束属于股票交易权限约束，不适用于 ETF 策略；创业板/科创板 ETF 仍可由 ETF 策略按其自身 universe 审计规则交易。

### 断言

```
S9-01  小盘个股策略默认从 `symbols` 和买入候选中过滤创业板 `300/301` 与科创板 `688/689` 股票，并在 `get_state()["parameters"]["excluded_board_prefixes"]` 暴露该规则
S9-02  ETF 策略不继承 A 股个股权限过滤，创业板/科创板相关 ETF 是否可交易由 ETF 策略自身 universe 与流动性/NAV 审计决定
S9-03  动态 PIT 个股 universe 中的全量候选集不得被 strict daily snapshot 当成每日硬依赖；策略应仅通过 `required_snapshot_symbols` 暴露风控指数等静态硬依赖
```

### 适用范围

- 适用于默认面向普通股票账户执行的 A 股个股策略，例如小盘财务过滤策略。
- 若用户明确提供已开通权限的账户配置，可通过策略参数覆盖该前缀列表，但默认研究和正式报告必须按普通账户可买股票池运行。

## CASE-10: Daily strategy runtime checkpoint

日线策略的运行时状态不能只存在于进程内存。`DailyBarStrategy` 的换仓 gate、策略内部持仓镜像，以及策略子类明确暴露的 checkpoint 字段，必须能序列化为 JSON-safe dict，并在 live/paper 新进程启动时恢复。DB 表结构保持固定，策略差异通过 `state_json` payload 表达，不允许用 pickle、`__dict__` 全量 dump 或回放历史订单来猜测状态。

### 断言

```
S10-01  DailyBarStrategy checkpoint/restore 必须恢复 _positions、_last_rebalance_date 和 _days_since_rebalance；恢复后 holding_days gate 的下一次状态转移必须与未重启进程一致
```

### 适用范围

- 适用于所有继承 `DailyBarStrategy` 且依赖 `holding_days`、risk regime、风险退出待处理集、目标权重缓存或其他跨日运行时变量的策略。
- `checkpoint_state()` 只保存影响未来状态转移的策略运行时变量；历史 bar buffer 仍由 warmup/PIT 数据重建，不能作为 checkpoint 的唯一来源。
- 策略子类如果新增会影响未来信号的变量，必须通过显式 hook 纳入 `state_json`，并补对应不变量或策略级回归测试。
