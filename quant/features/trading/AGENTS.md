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
- Live/Paper strategy-mode BUY orders must pass an `available_cash` hard risk check against that strategy's shadow portfolio cash, including pending order value; a strategy may reuse returned/earned cash but must not borrow from the shared real account.
- Live/Paper strategy-mode SELL orders must call `RiskEngine.record_order()` for rate tracking only; they must not add `order_value` into `_pending_order_values` or reduce another same-strategy BUY's available cash.
- Strategy-mode BUY fills must debit cost plus commission from that strategy's shadow portfolio cash; the master account cash must not be used again after initial allocation.
- Strategy `Context` must expose only a scoped order surface: no raw broker/execution manager, no global order list, and submitted orders must be attributed to the owning strategy.
- Current daily `MARKET` execution-cost contract: D-day strategy price or D-day signal-bar close only determines `execution_cost_bps`; actual live/paper `LIMIT` price is anchored to the execution-day open/reference price when the order is submitted. Pending-only D-close generation records the bps budget and must not persist a D-close-derived limit price.
- Live and backtest must share strategy signal generation, cost-protection target semantics, risk/cash gates, strategy attribution, control-state gating, and dashboard lifecycle status semantics. The only live-only differences are DB-backed state bridging and QMT real order submission/fill facts (price, quantity, commission, rejection, partial fill, broker IDs).
- 带 `strategy_name` 的实盘成交只能派发给所属策略，不能广播到其他策略
- Unassigned or `default` live broker fills must remain manual-account facts and must not be dispatched to managed strategy `on_fill` hooks.
- Live/Paper `MARKET + D-known price` 信号单经成本保护执行器转为 LIMIT；显式 LIMIT 保持原限价；到期目标单撤单并丢弃
- 默认 `live_trading.daily_snapshot_mode=True`：BAR 事件只进入日线快照缓存；`MARKET_CLOSE` 标记快照完成；下一次 `MARKET_OPEN` 用最新完整交易日快照批量触发所有策略，顺序为 feed all → mark portfolios by D close → after_trading all；缺失 required symbol 时跳过对应策略
- Live/Paper daily `MARKET` target cost bps must prefer strategy D-day price or D-day signal-bar close; `ExecutionReferencePriceResolver` supplies the execution-day open/reference anchor for actual LIMIT submission, and orders without that anchor are dropped unless `record_pending_only=True`.
- QMT 实盘成交必须把券商佣金传入 FillHandler；缺少费用字段时，A股/ETF 按成交额费率与 5 元起点估算，并摊入策略持仓 `avg_cost`
- PaperBroker simulated fills must use the shared `quant.runtime.execution_simulator` and `quant.runtime.execution_commission` models used by backtests; marketable LIMIT orders fill at their submitted limit price, while MARKET orders use execution-price slippage/impact; cash, avg_cost, fill callbacks, recorder rows, and dashboard NAV must all reflect the same fill price, quantity, rejection, and commission decisions.
- PaperBroker strategy-mode simulated fills must use the injected portfolio resolver so the execution simulator checks the owning strategy SubPortfolio for SELL/T+1/cash gates, not the shared master portfolio.
- OrderManager must inspect broker status after submit; broker-side rejected paper orders are terminal `strategy_orders` rejected facts with the broker order id and rejection reason, not submitted/no-fill rows.

## 修改守则

- 改引擎逻辑：只动 `engine.py`
- 改风控规则：只动 `risk.py`
- 改组合计算：只动 `portfolio.py`
- 改调度逻辑：只动 `scheduler.py`
- 改实盘下单归因、成本限价、成交记录：优先动 `infrastructure/execution/` 和 `quant_system.py` 组合根，不要让 `features/` 直接依赖 `infrastructure`

- One-shot paper/sim daily runs must inject `BAR -> MARKET_CLOSE -> MARKET_OPEN` through `Engine.inject_daily_snapshot()`; do not call strategy `on_after_trading()` directly from a separate simulation path.
- Live/Paper daily snapshots must call the shared runtime `run_daily_snapshots()` entry; execution adapters may differ, but D-close signal generation must not.
- Paper/sim runs using a real broker adapter must reject `trade_mode=REAL` unless `system.mode=live`.
- Paper mode is a local DB-backed backtest route: initialize only `PaperBroker`, load execution-date and previous bars, call the shared runtime execution simulator, and send local fills through the same FillHandler/recorder path.
- CLI one-shot daily runs default `--simulate-daily` to paper, but explicit `--simulate-daily --mode live` must preserve live mode for real-broker live daily execution.
- Live morning scripts must not paper-fill before execution-date bars exist; post-close data update drives paper replay with the same signal/execution date pair, and paper never copies manual live repair orders.
- Live/Paper strategy control (`strategy_dashboard.duckdb`) gates new mode-specific strategy signals only; it must not block portfolio marks, curve updates, metrics, or record reads.
- Paper mode must write recorder and strategy-position state to `strategy_dashboard.duckdb` with `mode='paper'`, separate from live mode rows.
- Paper mode may install the same cost-protection target-order executor, but it must still route only to local `PaperBroker`; it must never connect QMT/Futu external broker adapters in paper mode.
- Strategy dashboard pending orders are read-only diagnostics: derive them from accepted D-day signals that have no matching submitted order/fill inside the current submit window, default missing submit dates to the next business day, display the D-day `execution_cost_bps` budget as `Submit +bps` instead of a limit price, infer `Submit +bps` from legacy D-close limit records when metadata is absent, expire older submit dates instead of carrying them forward, tolerate broker IDs/tick-rounded execution prices/retry timestamps that differ from client D-close signals, and keep failed submit signals visible with `display_status=failed`.
- Strategy dashboard signal rows must show strategy-generated D-day signals only; execution-day submit attempts and failed submit records may feed order matching and action status, but must not inflate the visible Signals table.
- Strategy dashboard order rows must come from the server-side display contract: include order-date open price, paper and live `fill_price` display the actual simulated/broker fill price, paper LIMIT `fill_price` is the submitted limit price after marketability succeeds, `limit_price` remains separate, commission is separate, and slippage satisfies `fill_price = open_price * (1 + slippage_bps / 10000)`.
- Paper due signals with execution-date market data but no matching submitted order or fill must still render an order row with `display_status=no_fill`; they must not disappear just because no broker/paper order id exists.
- Strategy dashboard holdings and metrics must reuse the same display contract: avg_cost includes the contract fill price plus commission, cash starts from per-strategy initial cash, and total NAV equals cash plus market value.
- Strategy dashboard initial allocation cash is immutable after strategy configuration; the dashboard may display it but must not edit `allocation_cash` or submit broker orders.
- Strategy dashboard may create the first mode configuration only through Start with a positive `initial_cash`; after that, the strategy-mode allocation is immutable and feeds the SubPortfolio shadow-account cash limit.
- Strategy dashboard controls are scoped by `strategy_name` and mode (`live`/`paper`): live actions must not change paper gates, and paper actions must not change live gates.
- Strategy dashboard lifecycle order is mode-scoped and fixed: configure/start -> restore positions/runtime checkpoint -> D close data/OSS -> D pending-only signal generation -> D+1 order execution -> fill/position/checkpoint/NAV sync -> next close snapshot; live and paper share signal generation, diverge only at execution adapters, and pause/stop/liquidating states block D+1 execution for that mode even when pending orders already exist.
- Live/Paper daily snapshot startup must restore strategy runtime `_positions`, SubPortfolio lots from `StrategyPositionTracker`, and flexible strategy checkpoint payloads from `strategy_runtime_states.state_json` before signal generation.
- Post-close live/paper pending generation must use `execution.record_pending_only=True`: record accepted D-day signals for D+1 submission immediately after D-day data/OSS update, write a signal-date strategy snapshot marker even on no-action days, update risk pending state, never require D+1 bars for signal generation, and never call broker `submit_order`.
- Post-close pending generation must be DB-idempotent by `(strategy_name, mode, signal_date, submit_date, symbol, side, quantity, order_type)` and must reuse the existing pending order id on rerun instead of inserting duplicate accepted pending rows with new random client order ids.
- QMT real-open live submission must run the submit-only DB path (`--submit-pending-only`) and must not run simulate-daily, daily snapshot signal generation, or strategy hooks while consuming prior-day pending signals.
- Paper D+1 replay must consume due pending DB signals through the same pending-submit path before falling back to daily hook replay; when due pending exists it restores through the signal day, skips strategy warmup/hooks, writes only execution order/fill facts, then records post-execution checkpoints and snapshots. Submitted, filled, cancelled/canceled, rejected, and failed order rows are terminal for that submit window and must not be retried by replay.
- Live startup may reconcile broker trade history through the broker port before strategy initialization; recovery must prefer persisted non-default strategy attribution from submitted/order rows when the in-memory order map is empty after restart; when QMT trade history is empty but order history reports filled orders, recovery may synthesize missing fills from filled order history using local order/tracker attribution; unmatched fills must be audited as unresolved/default attribution instead of being silently assigned to another strategy.
- Strategy dashboard `liquidate_stop` creates a mode-scoped liquidation plan from tracked positions and marks the control state; it must not directly submit broker orders.
- Strategy dashboard payloads must expose per-mode operations ledger, recovery status, and top-level operations health so interrupted runs are visible before the next D-close/D+1 action cycle.
- Strategy dashboard payloads must read strategy records, controls, positions, and snapshots from `strategy_dashboard.duckdb`; legacy daily JSONL is valid only as one-time input to `migrate_jsonl_to_duckdb.py` and must not be read by dashboard/server runtime.
- Strategy dashboard state is migrated into `strategy_dashboard.duckdb` tables. Legacy daily JSONL may be read only by migration tooling; dashboard status, records, holdings, metrics, pending orders, curves, operations health, controls, and liquidation plans must read from the DuckDB state store.
- Strategy dashboard run status is an anomaly detector rendered as a continuous three-trading-day timeline: T-2 数据OK -> T-2 策略信号 -> T-1 提交订单 -> T-1 数据OK -> T-1 策略信号 -> T 提交订单 -> T 数据OK -> T 策略信号. 策略信号 must use filtered strategy-generated signal rows, not raw execution ledger rows; broker fills, submit attempts, and recovered order facts belong under the next trading day's 提交订单 details. Once due, 提交订单 owns submit/fill reconciliation: all due quantity filled is green, zero filled is red, and partial filled is yellow, with per-signal submitted/filled quantities capped at the signal quantity.
- Strategy dashboard run status timeline items must expose server-side `date`/`key`/`label`/`expected`/`observed`/`decision` evidence; signal/order items also expose `details`, and the UI must render compact clickable action tags instead of inferring status client-side.
- Configured running/paused live or paper modes with no signals/orders/fills must still materialize a cash-only `strategy_nav_snapshots` row for the latest market data date.
- Strategy dashboard mode controls must avoid no-op actions: render Start only for stopped/liquidating modes, show already-started plus Pause while running, show Resume while paused, and render first-start Initial Cash plus Start for not-configured modes.
- Strategy dashboard live and paper views are separate `/live` and `/paper` mode subpages that share the same component functions; do not reintroduce split live/paper tables in one stacked page.
- Strategy dashboard `/`, `/live`, and `/paper` entrypoints all serve the no-cache dashboard page; each mode view must expose immutable Initial Cash only in the top mode/overview area; the Windows launcher opens port 8791 and restarts stale payloads that do not expose per-mode initial cash; the page must reload when the served dashboard asset version changes.
- Strategy dashboard equity curves must align all rendered series to a shared trading-date axis, carry forward each series' last value when a date is missing, map x-coordinates by trading-day index rather than calendar-day milliseconds, never publish live/paper NAV points later than the latest local market data date, include an initial-cash baseline before the first filled trading date, derive missing strategy NAV curve points from filled order rows plus daily closes when snapshots are absent, normalize benchmark only from the strategy curve start, and show data freshness plus latest scheduled job status/error when market data, holdings, signals, or pending orders lag.
- Strategy dashboard holdings and performance must not mark a position with a close price earlier than that symbol's latest fill/order activity date; such rows stay `unmarked_after_activity`, use fill price as temporary market value, and immediately deduct known commissions from current NAV/PnL until matching market data arrives.
- CN live/paper schedulers must use the real CN trading-calendar resolver for open-day checks, next trading dates, and replay signal/execution dates; weekday-only date guesses are only a final fallback when no calendar/status/market data is available.
- Cross-day live/paper daily jobs must stamp DB records by business stage: pending-only accepted signals use the D-day signal timestamp and D+1 submit_date, while D+1 submit/reject/order records use execution_date market open instead of the process wall-clock date.
- QMT live trade-history recovery is a read-only post-close job: it may connect to the real broker and import missing fills, but it must not run `--simulate-daily`, `--pending-only`, or broker order submission. QMT recovery and recovery-poll scheduler entrypoints must skip non-trading days before connecting to QMT. Post-close live pending generation should not continue after recovery fails. A morning real-order submit task is allowed only as a machine-local ignored scheduler entrypoint with scheduler-role guard, real-order owner guard, optional pending-only generation for `latest-data --on-or-before scheduler_date` when no pending orders exist, `-ConfirmRealOrders`, QMT open-price polling from 09:29 with no broker submission before 09:30, and a per-execution-date marker/lock to prevent duplicate submissions. If the scheduler date is not a trading day, it may generate pending orders for the previous completed trading day but must not submit broker orders.
- QMT real-open duplicate-submit checks must only treat execution-date submit/fill facts as complete; prior execution rows whose `signal_date` equals the current D-close signal date must not block the D+1 real-order submission.
- Strategy dashboard live broker snapshots must be TTL-cached and disk-guarded; dashboard refreshes must not open a new QMT/xtquant connection each time or keep writing `userdata_mini` queue files when the QMT drive is low on free space.

## Known Pitfalls

- `Engine._on_data()` 使用 `hasattr(self.broker, 'update_price')` 多态调用，不使用 isinstance
- 策略通过 Context 访问 scoped 策略级能力，不要直接引用 Engine、broker、全局 OrderManager
- `RiskEngine._check_order_rate()` 回测模式（as_of_date != None）用 `_daily_order_count` 日计数器替代 wall clock，实盘模式仍用 `_order_timestamps` 时间戳列表
- Backtest order-rate limiting is disabled by default; set `risk.max_orders_per_day` or explicit `risk.max_orders_minute` when a daily order cap is intended.
- `RiskEngine._check_position_size()` 累计 `_pending_order_values[symbol]` 防止同日多次下单绕过仓位限制，`record_order()` 负责更新此字典
- `Portfolio.update_position()` 的 `realized_pnl` 参数仅对 SELL（quantity < 0）路径生效，BUY 路径忽略该参数
- `Context.submit_order()` 把 `MARKET` 订单交给 execution_manager；日线成本参考价优先来自策略 price 或 D 日 signal bar close，resolver 只提供执行日 open/reference anchor；显式 LIMIT 单必须直接传给 OrderManager
- 日线策略需要逐 BAR 实时回调时必须显式设置 `live_trading.feed_intraday_bars=True`；否则默认只在次日开盘处理上一交易日完整快照
- 实盘中断恢复时，日线快照模式只消费最新完成快照，不补发更早交易日的过期信号
- `live_trading.execution_reference.allow_strategy_price_fallback=True` 只影响 resolver 自身取不到 quote 时是否允许策略价作为执行 anchor；日线成本 bps 仍应优先使用 D 日信号参考价

## Dashboard Projection Boundary

- `dashboard_projection.project_holdings(...)`, `project_performance(...)`, `project_run_status_bar(...)`, `project_pending_orders(...)`, `project_signal_rows(...)`, `project_fill_rows(...)`, `project_order_rows(...)`, and `project_execution_summary(...)` own strategy-dashboard read-model semantics for holdings, cash, NAV, performance, equity-curve, lifecycle-status, pending-action, visible-signal, order-display, fill-display, commission, and slippage DTOs.
- Dashboard server/adapters collect facts from DB, broker snapshots, recorder outputs, config, calendar, and market data only; they may precompute adapter facts such as projected submit dates, signal close prices, and open prices, but must delegate valuation, curve-date, lifecycle-status, pending-action, visible-signal, order-display, fill-display, commission, and slippage semantics to `quant.features.trading.dashboard_projection`.
- UI code renders projection DTOs and must not infer lifecycle, cash, valuation, curve, pending-action, visible-signal, order-display, fill-display, commission, or slippage status client-side.
