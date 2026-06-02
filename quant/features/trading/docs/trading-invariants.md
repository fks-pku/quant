# Trading 不变量

Trading 模块管理组合状态、风控检查、SubPortfolio 隔离。

---

## 通用约定

| 组件 | 职责 |
|------|------|
| `Portfolio` | 单一资金池，线程安全，跟踪持仓/现金/NAV |
| `SubPortfolio` | 虚拟子账户，cash setter 同步 master |
| `RiskEngine` | 订单提交前风控检查：仓位、行业、日损、杠杆、频率 |

### 核心不变式

- T1 `NAV == cash + sum(position.market_value)`
- T2 `Portfolio.update_position` BUY 路径忽略 `realized_pnl` 参数
- T3 `SubPortfolio.cash` setter 将 delta 镜像到 `master.cash`
- T4 `master.cash` 始终等于所有子组合的 cash 之和加上储备
- T5 `RiskEngine.reset_daily()` 清零 `_daily_order_count` 和 `_pending_order_values`
- T6 `Position.remove_sell_lots` 按 FIFO 顺序消耗 lots
- T8 实盘策略分账时，OrderManager 必须使用该策略的 RiskEngine，FillHandler 必须更新该策略的 SubPortfolio
- T9 带 `strategy_name` 的实盘成交只派发给所属策略，不广播给其他策略
- T10 实盘 `MARKET + reference price` 信号单必须经限成本执行器转换为 LIMIT 单；到期未完成的目标单必须撤单并标记 dropped
- T11 默认日线实盘策略必须用完整 EOD 快照触发，D 日 BAR 不即时下单，D+1 `MARKET_OPEN` 才用 D 日完整快照生成信号
- T12 实盘 MARKET 目标单的 reference price 必须来自 broker/data-provider 执行行情 resolver；默认不得使用策略传入价兜底

---

## CASE-1: Portfolio 单标的 BUY-HOLD-SELL

### 前置条件

`Portfolio(initial_cash=100_000)`, symbol="AAPL"

### 操作序列

1. `update_position("AAPL", +100, 150.0, 15000.0)` — BUY
2. `update_market_price(160.0)`
3. `update_position("AAPL", -100, 160.0, 0, realized_pnl=1000.0)` — SELL

### 预期状态变化

| 步骤 | cash | qty | avg_cost | market_value | NAV |
|------|------|-----|----------|-------------|-----|
| 初始 | 100,000 | 0 | 0 | 0 | 100,000 |
| BUY | 100,000 | 100 | 150.0 | 15,000 | 115,000 |
| 价格更新 | 100,000 | 100 | 150.0 | 16,000 | 116,000 |
| SELL | 100,000 | 0 | 0 | 0 | 100,000 |

注意：Portfolio 本身不修改 cash（由 engine 层处理），只跟踪持仓。

### 断言

```
T1-01  pos_after_buy.qty==100, avg_cost==150.0
T1-02  after price update: market_value==16000, unrealized_pnl==1000
T1-03  after sell: pos.quantity==0, pos.avg_cost==0, pos.realized_pnl==1000
T1-04  NAV == cash + sum(market_value) at every step
```

### 对应测试: `test_t1_*` in `test_trading_invariants.py`

---

## CASE-2: Portfolio 多批次 BUY + 部分卖

### 前置条件

`Portfolio(initial_cash=100_000)`, symbol="AAPL"

### 操作序列

1. `update_position("AAPL", +100, 100.0, 10000.0, trade_date=D1)`
2. `update_position("AAPL", +100, 120.0, 12000.0, trade_date=D2)`
3. `update_position("AAPL", -150, 130.0, 0, realized_pnl=4500.0)`

### 预期状态变化

- BUY 100@100 → qty=100, avg_cost=100
- BUY 100@120 → qty=200, avg_cost=(10000+12000)/200=110
- SELL 150 → qty=50, FIFO: 100@100 + 50@120 consumed, remaining lot=50@120
- recalc_avg_cost → avg_cost=120.0

### 断言

```
T2-01  after 2 buys: qty==200, avg_cost==110.0
T2-02  after sell: qty==50, pos.realized_pnl==4500
T2-03  remaining lots: 1 lot with qty==50, price==120.0
T2-04  recalc_avg_cost==120.0
```

### 对应测试: `test_t2_*` in `test_trading_invariants.py`

---

## CASE-3: SubPortfolio cash 同步 master

### 前置条件

`master = Portfolio(initial_cash=100_000)`
`subA = SubPortfolio("A", 40_000, master)`
`subB = SubPortfolio("B", 60_000, master)`

### 操作序列

1. `subA.cash -= 10_000` (模拟买入扣款)
2. `subB.cash -= 20_000`

### 预期状态变化

- subA.cash: 40,000 → 30,000, master.cash += -10,000 → 90,000
- subB.cash: 60,000 → 40,000, master.cash += -20,000 → 70,000

### 断言

```
T3-01  master.cash == 70,000
T3-02  subA.cash == 30,000, subB.cash == 40,000
T3-03  sum(sub.cash) + reserve == master.cash (reserve=0 initially)
T3-04  subA.cash setter negative clamped to 0
```

### 对应测试: `test_t3_*` in `test_trading_invariants.py`

---

## CASE-4: SubPortfolio 同标的隔离持仓

### 前置条件

同 CASE-3 setup

### 操作序列

1. `subA.update_position("AAPL", +50, 150.0, 7500.0, trade_date=D1)`
2. `subB.update_position("AAPL", +30, 150.0, 4500.0, trade_date=D1)`
3. `subA.update_position("AAPL", -20, 160.0, 0, realized_pnl=200.0)`

### 断言

```
T4-01  subA.get_position("AAPL").qty == 30
T4-02  subB.get_position("AAPL").qty == 30
T4-03  总持仓 = 30 + 30 = 60 (各子组合独立)
T4-04  subA positions 不含 subB 的持仓
```

### 对应测试: `test_t4_*` in `test_trading_invariants.py`

---

## CASE-5: RiskEngine 仓位限制

### 前置条件

`Portfolio(initial_cash=100_000)`, `RiskEngine(config, portfolio)` with `max_position_pct=0.20`

### 操作序列

1. 买入 AAPL $10,000（NAV=$100,000, limit=$20,000）→ 通过
2. 再买入 AAPL $12,000（existing=$10,000, pending=$10,000, new=$12,000, total=$32,000）→ 拒绝

### 断言

```
T5-01  首次 $10k 订单通过
T5-02  record_order 后 pending 更新
T5-03  第二次 $12k 超限被拒
T5-04  reset_daily 清零 pending
```

### 对应测试: `test_t5_*` in `test_trading_invariants.py`

---

## CASE-6: RiskEngine 日损失限制

### 前置条件

`Portfolio(initial_cash=100_000)`, `RiskEngine` with `max_daily_loss_pct=0.05`

### 操作序列

1. 持仓跌至 NAV=96,000 → loss=4% → 通过
2. 持仓跌至 NAV=93,000 → loss=7% → 拒绝

### 断言

```
T6-01  4% loss 允许下单
T6-02  7% loss 拒绝下单
```

### 对应测试: `test_t6_*` in `test_trading_invariants.py`

---

## CASE-7: RiskEngine CN T+1 结算

### 前置条件

`Portfolio`, symbol="600519", 已持仓 1000 股于 D2 买入

### 操作序列

1. D1 卖出 1000 股 → `fill_date=D2`, `settled_quantity(D2)==0` (D2 lot_date 不小于 D2) → 拒绝
2. D2 卖出 1000 股 → `fill_date=D3`, `settled_quantity(D3)==1000` (D2 < D3) → 通过

### 断言

```
T7-01  信号日早于买入日时卖出被拒 (settled==0)
T7-02  信号日=买入日次日时卖出通过 (settled==1000)
T7-03  US symbol 不受 T+1 限制
```

### 对应测试: `test_t7_*` in `test_trading_invariants.py`

---

## CASE-8: Live 策略分账与成交归因

### 前置条件

两个策略共享同一个真实 broker，但在 `Engine` 内各有独立 `SubPortfolio` 和 `RiskEngine`。

### 操作序列

1. `DemoStrategy` 提交 `600519 BUY 100 @ 10.0`
2. OrderManager 执行风控检查并提交 broker
3. broker 回报 `DemoStrategy` 的成交

### 断言

```
T8-01  OrderManager.check_order 使用 DemoStrategy 对应的 RiskEngine
T8-02  record_order(symbol, order_value) 写入该策略的 pending order value
T8-03  broker_order_id 能反查到 DemoStrategy
T8-04  FillHandler 只更新 DemoStrategy 的 SubPortfolio
T8-05  QuantSystem._on_fill 只调用 DemoStrategy.on_fill，不调用其他策略
```

### 对应测试: `test_order_manager_uses_strategy_specific_risk_engine`, `test_fill_handler_updates_strategy_specific_portfolio`, `test_quant_system_dispatches_fill_only_to_owning_strategy` in `test_live_trading_records.py`

---

## CASE-9: Live 限成本目标单与到期丢单

### 前置条件

`LiveExecutionManager(default_max_cost_bps=25, default_deadline="14:50")`，策略通过 `Context.submit_order(..., order_type="MARKET", price=reference_price)` 发出信号量。

### 操作序列

1. BUY 信号 `reference_price=10.0` 转为 LIMIT 单
2. SELL 信号 `reference_price=10.0` 转为 LIMIT 单
3. 目标单超过当日截止时间仍未完成

### 断言

```
T9-01  BUY limit_price == reference_price * (1 + max_cost_bps / 10000)
T9-02  SELL limit_price == reference_price * (1 - max_cost_bps / 10000)
T9-03  显式 LIMIT 单保持原限价，不再套用执行器成本 bps
T9-04  未完成目标单到期后调用 cancel_order(order_id)
T9-05  dropped 状态表示当日未下完的剩余目标被丢弃，不跨日补单
T9-06  已成交、已撤销或已拒绝的目标单到期时不得再次撤单
```

### 对应测试: `test_live_execution_manager_*`, `test_trading_context_*` in `test_live_trading_records.py`

---

## CASE-11: Live 日线快照触发时点

### 前置条件

`Engine(live_trading.daily_snapshot_mode=True, strict_daily_snapshot=True)`，策略 required symbols 为 `["600519", "000001"]`。

### 操作序列

1. D 日收到 `600519` 和 `000001` 的 BAR
2. D 日 `MARKET_CLOSE` 只标记快照完成，不调用策略 `on_after_trading`
3. D+1 `MARKET_OPEN` 用 D 日完整快照调用 `on_data_batch` 和 `on_after_trading`
4. 若 D 日快照缺少任一 required symbol，则 D+1 跳过策略信号生成
5. 若中断恢复后存在多个完成快照，则只消费最新完成快照，旧快照不得补发过期信号

### 断言

```
T11-01  D 日 BAR 和 MARKET_CLOSE 不提交策略订单
T11-02  D+1 MARKET_OPEN 才用 D 日完整快照触发 on_data_batch/on_after_trading
T11-03  batch 顺序按策略 symbols 排列，避免实盘事件到达顺序影响策略输入
T11-04  缺失 required symbol 时不调用 on_data_batch/on_after_trading，不提交订单
T11-05  gap 恢复时只消费最新完成快照，不按旧日期补下单
```

### 对应测试: `test_daily_snapshot_runner_requires_complete_symbol_batch`, `test_trading_engine_runs_completed_daily_snapshot_on_next_market_open`, `test_trading_engine_skips_incomplete_daily_snapshot`, `test_trading_engine_uses_latest_completed_snapshot_after_gap` in `test_live_trading_records.py`

---

## CASE-12: Live MARKET 执行参考价来源

### 前置条件

策略在 T+1 开盘提交 `Context.submit_order(..., order_type="MARKET", price=strategy_price)`，`Context` 配置了 `ExecutionReferencePriceResolver` 和 `LiveExecutionManager`。

### 操作序列

1. broker quote 返回 `open_price=12.0`
2. 策略传入 `price=10.0`
3. `Context.submit_order()` 生成 live target order
4. broker quote 缺失时再次提交 MARKET target

### 断言

```
T12-01  target reference_price 使用 broker open_price=12.0，不使用策略 price=10.0
T12-02  LiveExecutionManager 以 resolver price 计算限成本 LIMIT
T12-03  resolver 取不到价格且未开启 fallback 时不得提交订单
T12-04  QMT broker quote 将 MiniQMT full_tick 的 openPrice/lastPrice 映射为 open_price/last_price
```

### 对应测试: `test_trading_context_uses_broker_reference_price_for_market_targets`, `test_trading_context_drops_market_target_when_reference_price_missing` in `test_live_trading_records.py`; `test_qmt_quote_reference_price_prefers_open_price` in `test_qmt_broker.py`
