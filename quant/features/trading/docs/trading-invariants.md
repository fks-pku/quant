# Trading 不变量

Trading 模块管理组合状态、风控检查、SubPortfolio 隔离。

---

Current daily execution-cost contract: D-day strategy price or D-day signal-bar close is a cost-reference input only. Live/paper target-order execution records the D-known `execution_cost_bps` budget after close, then computes the executable `LIMIT` from execution-day open/reference price at submission time. Pending-only signals display `+x bps` and must not persist a D-close-derived limit price.

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
- T10 live/paper `MARKET + D-known reference price` 信号单必须经限成本执行器转换为 LIMIT 单；到期未完成的目标单必须撤单并标记 dropped
- T11 默认日线实盘策略必须用完整 EOD 快照触发，D 日 BAR 不即时下单，D+1 `MARKET_OPEN` 才用 D 日完整快照生成信号
- T12 live/paper MARKET 目标单必须分离 D 日成本参考价与执行日 anchor：成本参考优先来自 D 日策略 price 或 signal bar close；broker/data-provider resolver 只提供执行日 open/reference anchor，不得兜底参与 D 日成本估算
- T13 Strategy dashboard must derive pending orders, order fill display, holdings, cash, NAV, and run freshness from one server-side display contract
- T14 QMT 实盘成交记录必须携带券商佣金；若 MiniQMT 成交回报缺少费用字段，A股/ETF 默认按成交额费率与 5 元起点估算，并摊入策略持仓成本

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

## CASE-5B: RiskEngine 影子账户 cash 限制

### 前置条件

`Portfolio(initial_cash=100_000)`, `RiskEngine` with `max_position_pct=1.0`, strategy shadow portfolio cash is the only callable cash for BUY orders.

### 操作序列

1. `portfolio.cash=25_000`, BUY order value is `30_000` -> rejected by `available_cash`.
2. `portfolio.cash` receives returned/earned cash to `30_000`, BUY order value is `30_000` -> accepted.
3. `FillHandler` processes a BUY fill for `SubPortfolio("DemoStrategy", 20_000, master)` with cost plus commission of `5_005` -> `sub.cash` becomes `14_995` and `master.cash` remains `80_000`.

### 断言

```
T5B-01 BUY order value above current shadow cash is rejected
T5B-02 Returned or earned cash increases the shadow account's callable cash
T5B-03 BUY fill deducts cost plus commission from strategy shadow cash without drawing extra master cash
```

### 对应测试: `test_t5b_*` in `test_trading_invariants.py`; `test_fill_handler_buy_deducts_strategy_shadow_cash` in `test_live_trading_records.py`

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
T8-06  Strategy Context exposes only a scoped order surface: no raw broker, no raw execution manager, no global order list, and cross-strategy submit attempts are rejected
```

### 对应测试: `test_order_manager_uses_strategy_specific_risk_engine`, `test_fill_handler_updates_strategy_specific_portfolio`, `test_fill_handler_buy_deducts_strategy_shadow_cash`, `test_quant_system_dispatches_fill_only_to_owning_strategy`, `test_context_exposes_strategy_scoped_order_surface_only` in `test_live_trading_records.py`

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
T9-01  Current BUY contract: D-day reference/signal bar estimates `execution_cost_bps`; executable BUY `LIMIT` is anchored to execution-day open/reference plus that bps budget.
T9-02  Current SELL contract: D-day reference/signal bar estimates `execution_cost_bps`; executable SELL `LIMIT` is anchored to execution-day open/reference minus that bps budget.
T9-03  显式 LIMIT 单保持原限价，不再套用执行器成本 bps
T9-04  未完成目标单到期后调用 cancel_order(order_id)
T9-05  dropped 状态表示当日未下完的剩余目标被丢弃，不跨日补单
T9-06  已成交、已撤销或已拒绝的目标单到期时不得再次撤单
T9-07  QMT FIX_PRICE 出站限价必须规整到交易所 tick；BUY 向下取整、SELL 向上取整，保持成本边界
T9-08  QMT trade_mode=SIMULATE 不是已验证沙盒下单通道，必须拒绝 submit_order；模拟盘使用 PaperBroker
T9-09  paper mode 只能初始化和运行 PaperBroker，不得连接 QMT/Futu 等外部交易 broker
T9-10  PaperBroker 使用执行日 open 做本地撮合；LIMIT 必须按回测 marketability 规则成交或拒绝，并通过 trade callback 进入统一 FillHandler
T9-11  live morning 与 post-close paper replay 必须使用同一 signal_date/execution_date；paper 等执行日 open 入库后按 LIMIT marketability 撮合，live/paper 成本限价均来自 D 日可知成本模型，不复制人工补单
T9-12  启用 execution_cost_model 时，live/paper 目标单 cost bps 使用 D 日信号 bar 的 close/ADV/volatility 估算；D+1 broker/data-provider quote 只能作为 LIMIT anchor，不能参与 D 日 cost bps 计算。
```

### 对应测试: `test_live_execution_manager_*`, `test_trading_context_*`, `test_paper_broker_uses_execution_open_for_limit_fill_and_callbacks`, `test_order_manager_flushes_paper_broker_fills_after_submission`, `test_quant_system_rejects_external_broker_adapter_in_paper_mode` in `test_live_trading_records.py`; `test_qmt_limit_price_rounds_to_exchange_tick_preserving_side_bound`, `test_submit_order_normalizes_limit_price_before_qmt_call`, `test_qmt_simulate_trade_mode_refuses_order_submission` in `test_qmt_broker.py`

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

## CASE-12: Live/Paper MARKET 成本保护参考价来源

### 前置条件

策略在 T+1 开盘提交 `Context.submit_order(..., order_type="MARKET", price=strategy_price)`，`Context` 配置了 `ExecutionReferencePriceResolver` 和成本保护执行器。

### 操作序列

1. broker quote 返回 `open_price=12.0`
2. 策略传入 D 日可知 `price=10.0`
3. `Context.submit_order()` 生成 live/paper target order
4. 策略 price 缺失时优先使用执行器缓存的 D 日 signal bar close
5. D 日 signal reference 也缺失时，不得用 broker/data-provider resolver 兜底估算成本；resolver 只提供执行日 anchor

### 断言

```
T12-01  target reference_price 优先使用策略传入的 D 日 price=10.0，不被 broker open_price=12.0 覆盖
T12-02  price 缺失时使用执行器缓存的 D 日 signal bar close
T12-03  D 日 reference 缺失且 resolver 取不到价格时不得提交订单
T12-04  QMT broker quote 将 MiniQMT full_tick 的 openPrice/lastPrice 映射为 open_price/last_price
```

### 对应测试: `test_trading_context_uses_strategy_reference_price_for_market_targets`, `test_trading_context_uses_signal_bar_reference_when_price_missing`, `test_trading_context_does_not_use_execution_reference_as_cost_reference`, `test_trading_context_drops_market_target_when_reference_price_missing` in `test_live_trading_records.py`; `test_qmt_quote_reference_price_prefers_open_price` in `test_qmt_broker.py`

---

## CASE-13: Live/Paper strategy control gate

### Preconditions

A strategy mode (`live` or `paper`) may be in one of the file-backed control states `running`, `paused`, `stopped`, or `liquidating`.

### Assertions

```
T13-01  Only running + enabled + no liquidation_requested may accept new strategy signals for that mode
T13-02  paused/stopped/liquidating strategies must not run daily snapshot hooks or submit orders through Context
T13-03  Mode control gates signal acceptance only; portfolio marks, curves, metrics, and record reads must continue
T13-04  Paper mode records and strategy positions must use paper_trading paths, never live_trading or live position state
T13-05  Dashboard control actions write strategy_controls.json only; they must not directly call broker submit_order
T13-06  Dashboard holdings and mode metrics must mark open positions with the latest DuckDB close, not stale fill prices or zero-valued snapshots
T13-07  Dashboard pending order list shows accepted D-day strategy signals that do not yet have a matching submitted order or fill inside the current submit window; pending rows display D-known `execution_cost_bps` in the `Submit +bps` column as `+x bps` instead of a D-close-derived limit price; legacy rows that predate `execution_cost_bps` infer `Submit +bps` from D-close and the stored D-close-derived limit; matching must tolerate broker order IDs that differ from client signal IDs and near-simultaneous submitted-order timestamps that are marginally earlier than the recorded signal timestamp; signals without explicit submit_date/execution_date default to the next real CN trading day; signals whose submit_date is before today must expire out of pending_orders so the next day is recalculated from strategy state instead of carrying old actions forward
T13-08  Dashboard order rows are filled only by the server-side display contract: open_price is the order-date daily open, paper fill_price equals limit_price, live fill_price equals broker fill price, commission is reported separately, and slippage_bps satisfies fill_price = open_price * (1 + slippage_bps / 10000)
T13-09  Dashboard holdings, cash, total NAV, and total return must be derived from the same order display contract: avg_cost includes contract fill price plus commission, cash starts from per-strategy initial cash, and NAV equals cash plus market value
T13-10  Live/Paper daily snapshot startup must restore strategy runtime _positions and strategy SubPortfolio lots from StrategyPositionTracker before signal generation
T13-11  Post-close live pending generation must run D-day strategy snapshots in record_pending_only mode: record accepted signals with D-day timestamps for D+1 submission, write a signal-date strategy snapshot marker even when no new orders are produced, preserve cash-only strategies as NAV/Cash equal to their initial allocation, update risk pending state, and never call broker submit_order
T13-12  Dashboard initial allocation cash is immutable after strategy configuration: allocation endpoints must reject changes, leave qmt_live_config/paper_config unchanged, and never submit broker orders
T13-12B Dashboard first Start for a not-configured strategy-mode must require positive initial_cash, append that mode's config entry, then start the mode; subsequent Start/control actions must not mutate the configured initial_cash.
T13-13  Strategy control state is scoped by strategy_name and mode: live actions must not change paper signal gates, paper actions must not change live signal gates, and dashboard controls must send the target mode explicitly
T13-14  Dashboard control actions must append strategy+mode scoped audit rows so pause/resume/stop/liquidate transitions survive process restarts
T13-15  Dashboard liquidate_stop creates a mode-scoped liquidation plan from tracked strategy positions and must not directly submit broker orders
T13-16  Dashboard payloads must expose per-mode operations ledgers, recovery status, and top-level operations health from durable records
T13-17  Live startup broker-history reconciliation must import missing broker fills idempotently before strategy initialization; unresolved order attribution must be audited instead of silently crossing strategy boundaries
T13-18  Dashboard mode controls must be state-appropriate: Start is rendered for stopped/liquidating modes, running modes show already-started status plus Pause, paused modes show Resume, and not-configured modes render a first-start Initial Cash input plus Start rather than no-op controls
T13-19  Dashboard live and paper views must be separate `/live` and `/paper` mode subpages that use the same component functions; live/paper data must not be shown as split tables in one stacked page
T13-20  Dashboard root, live, and paper entrypoints must all serve the no-cache dashboard page, each mode view must expose immutable Initial Cash only in the top mode/overview area, the launcher must open the current 8791 dashboard and restart stale payloads that do not expose per-mode initial cash, and the page must reload when the served dashboard asset version changes
T13-21  Dashboard equity curves must align all series by trading date, include an initial-cash baseline before the first filled trading date, normalize benchmark only from the strategy curve start, derive missing strategy NAV curve points from filled order rows plus daily closes when durable snapshots are absent, and stale dashboard data must expose expected market date plus latest scheduled job status/error
T13-22  Each strategy_name and mode must maintain canonical append-only strategy-mode records under strategy_modes/<mode>/<strategy_name>/ for operations, signals, orders, fills, and snapshots; legacy daily rows are materialized into that source, snapshots are canonicalized from that mode's filled order ledger plus initial cash and daily closes, and dashboard status, records, holdings, metrics, pending orders, and curves must be derived from that strategy-mode source, never by mixing another strategy or mode.
T13-23  A configured running/paused live or paper mode must maintain a cash-only strategy-mode snapshot for the latest market data date even when there are no signals, orders, or fills, so equity curves and latest NAV show initial cash rather than disappearing.
T13-24  CN live/paper post-close scheduling must resolve open days, next trading days, and replay signal/execution dates through the real CN trading-calendar resolver; weekday-only calendar guesses are not allowed except as a final fallback when neither calendar cache nor local market/status data is available.
T13-25  The QMT live trade-history recovery job is read-only: it may connect to the live broker and import missing broker fills into strategy-mode ledgers, but it must not run simulate-daily, pending-only signal generation, or broker order submission. Post-close live pending generation must wait for successful recovery; no 09:30 real-order task should be enabled by this recovery path.
```

### Tests: `test_strategy_control_actions_gate_live_signals`, `test_strategy_control_actions_are_mode_isolated`, `test_quant_system_live_signal_gate_reads_strategy_control_file`, `test_quant_system_paper_signal_gate_reads_paper_control_file`, `test_quant_system_paper_mode_uses_separate_recorder_and_tracker`, `test_live_trading_recorder_writes_strategy_mode_records`, `test_quant_system_restores_strategy_runtime_positions_from_tracker`, `test_quant_system_allocates_default_live_strategy_cash`, `test_order_manager_record_pending_only_does_not_submit_to_broker`, `test_quant_system_pending_only_daily_snapshot_records_signal_day_snapshot_marker`, `test_trading_engine_signal_gate_blocks_daily_snapshot_orders`, `test_context_signal_gate_blocks_direct_order_submission` in `test_live_trading_records.py`; `test_strategy_dashboard_payload_reads_live_records_positions_and_controls`, `test_strategy_dashboard_materializes_and_reads_strategy_mode_records`, `test_strategy_dashboard_cash_only_configured_strategy_uses_initial_cash`, `test_strategy_dashboard_derives_live_curve_from_fills_when_snapshots_missing`, `test_strategy_dashboard_does_not_mark_near_timestamp_order_as_pending`, `test_strategy_dashboard_expires_past_submit_date_from_pending_orders`, `test_strategy_dashboard_pending_orders_default_to_next_trading_date`, `test_strategy_dashboard_renders_only_state_appropriate_mode_actions`, `test_strategy_dashboard_uses_mode_subpages_with_shared_components`, `test_strategy_dashboard_serves_live_and_paper_subpages`, `test_strategy_dashboard_launcher_opens_current_port_and_restarts_stale_payload`, `test_strategy_dashboard_surfaces_scheduled_job_failures`, `test_strategy_dashboard_surfaces_live_recovery_job_status`, `test_strategy_dashboard_start_action_enables_paper_mode`, `test_strategy_dashboard_start_unconfigured_mode_assigns_initial_cash`, `test_strategy_dashboard_start_unconfigured_mode_requires_initial_cash`, `test_strategy_dashboard_allocation_endpoint_rejects_configured_cash_change`, `test_strategy_dashboard_control_endpoint_updates_modes_independently`, `test_strategy_dashboard_liquidate_stop_creates_mode_scoped_plan` in `test_strategy_dashboard_server.py`; `test_strategy_mode_record_store_isolates_strategy_and_mode`, `test_strategy_control_action_writes_mode_operation`, `test_sync_broker_trade_history_imports_missing_fills_once`, `test_sync_broker_trade_history_marks_unknown_order_attribution` in `test_strategy_operations_ledger.py`; `test_cn_trading_calendar_uses_cached_real_holiday_window`, `test_cn_trading_calendar_latest_two_data_dates_use_common_available_sources` in `test_cn_trading_calendar.py`; `test_scheduled_scripts_replay_paper_after_post_close_data_update` in `test_paper_backfill_from_live_records.py`

---

## CASE-14: QMT 实盘成交佣金归因

### 前置条件

QMT `REAL` 成交回报 `518880 BUY 1000 @ 9.302`，成交回报本身没有 commission 字段。

### 操作序列

1. `_qmt_trade_callback()` 收到 MiniQMT 成交回报
2. QMTBroker 按国金 A股/ETF 佣金口径估算交易佣金：`max(成交金额 * configured_rate, 5.0)`
3. FillHandler 写入 recorder 并更新 StrategyPositionTracker

### 预期状态变化

| 项目 | 数值 |
|------|------|
| 成交额 | 9,302.00 |
| 估算佣金 | 5.00 |
| 策略 avg_cost | (9,302.00 + 5.00) / 1000 = 9.307 |

### 断言

```
T14-01  QMT trade callback payload includes commission==5.0 for 518880 1000@9.302
T14-02  StrategyPositionTracker BUY avg_cost includes fill commission
T14-03  QMT broker trade-history rows expose normalized order_id, fill_id, symbol, side, quantity, price, commission, and timestamp for live restart reconciliation
```

### 对应测试: `test_qmt_trade_callback_estimates_cn_etf_minimum_commission`, `test_qmt_trade_history_maps_xtquant_trade_records` in `test_qmt_broker.py`; `test_fill_handler_passes_commission_to_strategy_tracker_cost` in `test_live_trading_records.py`
