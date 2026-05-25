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
- 如果未来抽象出公共日线调仓基类，应把本 CASE 的状态机迁移到公共基类测试，并保留策略级回归测试验证接入正确。

## CASE-6: Top-level strategy promotion gate

顶层 `quant/features/strategies/<strategy_id>/` 是生产注册表自动发现区，不再存放所有研究候选。只有严格本地回测报告证明 `CAGR > 10%` 的策略，才能留在顶层。其它策略必须迁入 `quant/features/strategies/reject/<strategy_id>/`，可用于审计、复现和单测，但不会被 `StrategyRegistry` 自动发现。

### 断言

```
S6-01  `strategies/reject/` 目录存在
S6-02  顶层每个含 strategy.py 的策略目录都必须有 strict last_result/grid_result/batch_result CAGR 证据
S6-03  顶层策略的 CAGR 必须 > 0.10
S6-04  `strategies/reject/<strategy_id>/strategy.py` 不参与默认目录自动发现
```

## CASE-7: PIT wide universe candidate selection

点时间 universe 的正确语义不是把候选池在回测起点压窄成少数代表，也不是用当前存续名单直接回溯。允许预先解析一个覆盖回测窗口的符号 superset，但每个调仓点只能让当时可见、已有 bar、PIT 数据、流动性和 lookback 证据的候选进入排序；持仓必须由信号从这个当时可见的宽候选池中选择。

### 断言

```
S7-01  同一类别中，调仓日可见的宽候选必须按策略信号排序，不得先按规模预选单个主代表再排序
S7-02  未来新发或当日无 current bar 的 symbol 即使在窗口 superset 中，也不得进入该调仓日的可选候选
```
