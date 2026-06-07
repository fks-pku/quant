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
- Strategy-mode BUY fills must debit cost plus commission from that strategy's shadow portfolio cash; the master account cash must not be used again after initial allocation.
- Strategy `Context` must expose only a scoped order surface: no raw broker/execution manager, no global order list, and submitted orders must be attributed to the owning strategy.
- 带 `strategy_name` 的实盘成交只能派发给所属策略，不能广播到其他策略
- Live/Paper `MARKET + D-known price` 信号单经成本保护执行器转为 LIMIT；显式 LIMIT 保持原限价；到期目标单撤单并丢弃
- 默认 `live_trading.daily_snapshot_mode=True`：BAR 事件只进入日线快照缓存；`MARKET_CLOSE` 标记快照完成；下一次 `MARKET_OPEN` 用最新完整交易日快照批量触发所有策略，顺序为 feed all → mark portfolios by D close → after_trading all；缺失 required symbol 时跳过对应策略
- Live/Paper 日线 `MARKET` 目标单的限价参考价优先使用策略传入的 D 日价格或 D 日 signal bar close；`ExecutionReferencePriceResolver` 只在信号参考价缺失时兜底，不得用 D+1 quote 放宽 D 日成本保护限价
- QMT 实盘成交必须把券商佣金传入 FillHandler；缺少费用字段时，A股/ETF 按成交额费率与 5 元起点估算，并摊入策略持仓 `avg_cost`

## 修改守则

- 改引擎逻辑：只动 `engine.py`
- 改风控规则：只动 `risk.py`
- 改组合计算：只动 `portfolio.py`
- 改调度逻辑：只动 `scheduler.py`
- 改实盘下单归因、成本限价、成交记录：优先动 `infrastructure/execution/` 和 `quant_system.py` 组合根，不要让 `features/` 直接依赖 `infrastructure`

- One-shot paper/sim daily runs must inject `BAR -> MARKET_CLOSE -> MARKET_OPEN` through `Engine.inject_daily_snapshot()`; do not call strategy `on_after_trading()` directly from a separate simulation path.
- Live/Paper daily snapshots must call the shared runtime `run_daily_snapshots()` entry; execution adapters may differ, but D-close signal generation must not.
- Paper/sim runs using a real broker adapter must reject `trade_mode=REAL` unless `system.mode=live`.
- Paper mode is a local backtest-style route: initialize only `PaperBroker`, load execution-date bars, fill against execution-date open, and send local fills through the same FillHandler/recorder path.
- CLI one-shot daily runs default `--simulate-daily` to paper, but explicit `--simulate-daily --mode live` must preserve live mode for real-broker live daily execution.
- Live morning scripts must not paper-fill before execution-date bars exist; post-close data update drives paper replay with the same signal/execution date pair, and paper never copies manual live repair orders.
- Live/Paper strategy control (`strategy_controls.json`) gates new mode-specific strategy signals only; it must not block portfolio marks, curve updates, metrics, or record reads.
- Paper mode must write recorder and strategy-position state under `quant/infrastructure/var/paper_trading/`, separate from live records and live positions.
- Paper mode may install the same cost-protection target-order executor, but it must still route only to local `PaperBroker`; it must never connect QMT/Futu external broker adapters in paper mode.
- Strategy dashboard pending orders are read-only diagnostics: derive them from accepted D-day signals that have no matching submitted order/fill inside the current submit window, default missing submit dates to the next business day, expire older submit dates instead of carrying them forward, and tolerate broker IDs that differ from client signal IDs.
- Strategy dashboard order rows must come from the server-side display contract: include order-date open price, paper fill price displays as limit price, live fill price displays as broker fill price, commission is separate, and slippage satisfies `fill_price = open_price * (1 + slippage_bps / 10000)`.
- Strategy dashboard holdings and metrics must reuse the same display contract: avg_cost includes the contract fill price plus commission, cash starts from per-strategy initial cash, and total NAV equals cash plus market value.
- Strategy dashboard initial allocation cash is immutable after strategy configuration; the dashboard may display it but must not edit `allocation_cash` or submit broker orders.
- Strategy dashboard may create the first mode configuration only through Start with a positive `initial_cash`; after that, the strategy-mode allocation is immutable and feeds the SubPortfolio shadow-account cash limit.
- Strategy dashboard controls are scoped by `strategy_name` and mode (`live`/`paper`): live actions must not change paper gates, and paper actions must not change live gates.
- Live/Paper daily snapshot startup must restore strategy runtime `_positions` and SubPortfolio lots from `StrategyPositionTracker` before signal generation.
- Post-close live pending generation must use `execution.record_pending_only=True`: record accepted D-day signals for D+1 submission, write a signal-date strategy snapshot marker even on no-action days, update risk pending state, and never call broker `submit_order`.
- Live startup may reconcile broker trade history through the broker port before strategy initialization; unmatched fills must be audited as unresolved/default attribution instead of being silently assigned to another strategy.
- Strategy dashboard `liquidate_stop` creates a mode-scoped liquidation plan from tracked positions and marks the control state; it must not directly submit broker orders.
- Strategy dashboard payloads must expose per-mode operations ledger, recovery status, and top-level operations health so interrupted runs are visible before the next D-close/D+1 action cycle.
- Strategy dashboard payloads must materialize legacy daily JSONL rows into canonical `strategy_modes/<mode>/<strategy_name>/` append-only records, canonicalize snapshots from that mode's filled order ledger plus initial cash and daily closes, then derive status, records, holdings, metrics, pending orders, and curves only from that strategy-mode source.
- Configured running/paused live or paper modes with no signals/orders/fills must still materialize a cash-only `strategy_modes/<mode>/<strategy_name>/snapshots.jsonl` NAV point for the latest market data date.
- Strategy dashboard mode controls must avoid no-op actions: render Start only for stopped/liquidating modes, show already-started plus Pause while running, show Resume while paused, and render first-start Initial Cash plus Start for not-configured modes.
- Strategy dashboard live and paper views are separate `/live` and `/paper` mode subpages that share the same component functions; do not reintroduce split live/paper tables in one stacked page.
- Strategy dashboard `/`, `/live`, and `/paper` entrypoints all serve the no-cache dashboard page; each mode view must expose immutable Initial Cash only in the top mode/overview area; the Windows launcher opens port 8791 and restarts stale payloads that do not expose per-mode initial cash; the page must reload when the served dashboard asset version changes.
- Strategy dashboard equity curves must align all series by trading date, include an initial-cash baseline before the first filled trading date, derive missing strategy NAV curve points from filled order rows plus daily closes when snapshots are absent, normalize benchmark only from the strategy curve start, and show data freshness plus latest scheduled job status/error when market data, holdings, signals, or pending orders lag.
- CN live/paper schedulers must use the real CN trading-calendar resolver for open-day checks, next trading dates, and replay signal/execution dates; weekday-only date guesses are only a final fallback when no calendar/status/market data is available.
- QMT live trade-history recovery is a read-only post-close job: it may connect to the real broker and import missing fills, but it must not run `--simulate-daily`, `--pending-only`, or broker order submission. Post-close live pending generation should not continue after recovery fails. A morning real-order submit task is allowed only as a machine-local ignored scheduler entrypoint with scheduler-role guard, real-order owner guard, optional pending-only generation for `latest-data --on-or-before scheduler_date` when no pending orders exist, `-ConfirmRealOrders`, QMT open-price polling from 09:29 with no broker submission before 09:30, and a per-execution-date marker/lock to prevent duplicate submissions. If the scheduler date is not a trading day, it may generate pending orders for the previous completed trading day but must not submit broker orders.

## Known Pitfalls

- `Engine._on_data()` 使用 `hasattr(self.broker, 'update_price')` 多态调用，不使用 isinstance
- 策略通过 Context 访问 scoped 策略级能力，不要直接引用 Engine、broker、全局 OrderManager
- `RiskEngine._check_order_rate()` 回测模式（as_of_date != None）用 `_daily_order_count` 日计数器替代 wall clock，实盘模式仍用 `_order_timestamps` 时间戳列表
- Backtest order-rate limiting is disabled by default; set `risk.max_orders_per_day` or explicit `risk.max_orders_minute` when a daily order cap is intended.
- `RiskEngine._check_position_size()` 累计 `_pending_order_values[symbol]` 防止同日多次下单绕过仓位限制，`record_order()` 负责更新此字典
- `Portfolio.update_position()` 的 `realized_pnl` 参数仅对 SELL（quantity < 0）路径生效，BUY 路径忽略该参数
- `Context.submit_order()` 把 `MARKET` 订单交给 execution_manager；日线信号参考价优先来自策略 price 或 D 日 signal bar close，resolver 只兜底；显式 LIMIT 单必须直接传给 OrderManager
- 日线策略需要逐 BAR 实时回调时必须显式设置 `live_trading.feed_intraday_bars=True`；否则默认只在次日开盘处理上一交易日完整快照
- 实盘中断恢复时，日线快照模式只消费最新完成快照，不补发更早交易日的过期信号
- `live_trading.execution_reference.allow_strategy_price_fallback=True` 只影响 resolver 兜底路径；日线成本保护限价仍应优先使用 D 日信号参考价
